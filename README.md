# swing-trading-bot

Autonomous AI swing trading system for Indian equities. Multi-agent sentiment
simulation, strict risk guardrails and post-trade learning memory. Paper trading only.

This repository is at **Phase 1: the headless sandbox**. It replays historical
news for NSE stocks through a multi-agent persona swarm and checks the swarm's
calls against what prices actually did. There is no broker code and nothing here
can place an order.

## Quick start

```bash
pip install -e ".[dev]"
pytest
swingbot-backtest --events data/sample/events.csv --prices data/sample/prices --benchmark NIFTY50
```

The sample is synthetic (see `data/sample/README.md`), so the printed numbers
only prove the pipeline runs.

## How it works

```
news event ──> swarm engine ──> Signal {ticker, sentiment, confidence, time_horizon}
                                   │
daily OHLC  ──> entry/exit rules ──┴──> hit / miss, return after costs ──> summary
```

1. **Events** (`src/swingbot/events.py`). CSV or JSONL with `event_id, ticker,
   published_at, headline` and optional `body, source`. Timestamps without an
   offset are read as IST.
2. **Swarm** (`src/swingbot/swarm/`). Anything implementing
   `SwarmEngine.simulate(event) -> Signal` plugs in. Engines never see prices.
   * `lexicon`: four personas (Retail Bull, Institutional Bear, FII desk,
     Financial Media) driven by a keyword tone model. Offline, free,
     deterministic. A baseline, not a strategy.
   * `claude`: the same personas, each role-played by Claude with structured
     output. Needs `pip install -e ".[llm]"` and an Anthropic API key. Cost is
     events x 4 personas x rounds calls, so start small.
   * `recorded`: scores persona views written to a JSONL file. This is how
     Claude Code plays the swarm with no API key (see below).
   * `mirofish`: placeholder adapter with the contract filled in and the call
     left as `NotImplementedError` until MiroFish is evaluated.
   * Personas run for `--rounds` rounds. From round 2 each persona sees the
     others' views and can update, which is the "social contagion" step.
     A `ReportAgent` turns the final round into one signal.
3. **Timing** (`src/swingbot/backtest.py`). News before 09:15 IST on a trading
   day enters at that day's open; anything later enters at the next session's
   open. The exit is the close of session N, where N = ceil(horizon / 24h), so
   72h means 3 sessions.
4. **Scoring** (`src/swingbot/metrics.py`). Hit rate with a coin-flip p-value,
   average return per call after a round-trip cost (default 25 bps), a
   confidence-weighted return, Brier score for confidence calibration, rank IC
   of crowd score against the move, an always-long baseline, and the return
   against a benchmark when one is given.

Each run writes `runs/<timestamp>-<engine>/results.csv` (one row per event,
including each persona's view) and `summary.json`.

## Running the swarm in Claude Code (no API key)

Open this repo in Claude Code and ask it to "run the swarm backtest". The
`swarm-backtest` skill in `.claude/skills/` has Claude Code:

1. write a news-only brief with `swingbot-briefs` (personas and headlines, no prices),
2. play each persona itself for two rounds and append the views to `runs/views.jsonl`,
3. only then score them with `swingbot-backtest --engine recorded --views runs/views.jsonl`.

It runs on your Claude plan instead of API credits. The tradeoffs: it only runs
while you have a session open (no unattended or scheduled runs), one model
plays every persona in a single context so the views are less independent than
separate API calls, and large event files need several batches. The views file
is saved, so a run can be re-scored or compared later without redoing it.

### Technicals and the Bull/Bear debate

Two ideas borrowed from [TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache 2.0; the code here is our own):

* `swingbot-briefs --with-technicals <prices dir>` adds a technical snapshot to
  each event: EMA10, SMA50/200, RSI14, MACD, Bollinger bands, ATR14, VWMA20,
  20-day range and relative volume (`src/swingbot/technicals.py`). It uses only
  sessions that had closed when the news broke: news at 11:00 does not get that
  day's close. Missing or short history is written as "unavailable", never as a
  flat chart. Without the flag the brief stays news-only, as before.
* After the persona rounds, Claude Code plays a Bull researcher, a Bear
  researcher and a judge. The judge's stance becomes the signal. Score the same
  views file with `--crowd-only` to see the persona crowd without the debate.

### Learning memory: the Verifier and past lessons

Blueprint Section 2. After trades close, Claude Code plays a Verifier that
reviews each one and writes a lesson; later briefs show the swarm the lessons
from similar past trades. All of it sits in one local SQLite file,
`runs/memory.db`, so it costs nothing and needs no API key.

```bash
swingbot-memory ingest --results runs/<run>/results.csv --run base --views runs/views.jsonl
swingbot-memory review --closed-before 2026-08-01 --out runs/verify.md   # Claude Code writes runs/lessons.jsonl
swingbot-memory learn --lessons runs/lessons.jsonl
swingbot-briefs --events <events> --with-lessons runs/memory.db --until 2026-08-08 --views runs/views-mem.jsonl
swingbot-memory compare --without runs/<base>/results.csv --with runs/<mem>/results.csv
swingbot-memory audit
```

* **Ledger** (`trades`): news, the swarm's call and reasoning, the outcome.
  Backtest trades close at 15:30 IST on their exit session; paper trades will
  use their fill time.
* **Lessons**: a verdict (`right_for_right_reason`, `right_for_wrong_reason`,
  `wrong`, `no_call`), one or two sentences, and tags.
* **Retrieval** (`src/swingbot/memory/retrieval.py`): TF-IDF word overlap on
  headline, lesson and tags, plus a bonus for the same stock. A lesson is only
  eligible for news published strictly after its trade closed, so a backtest
  can't learn from a trade that was still open. Every retrieval is logged and
  `swingbot-memory audit` fails if any broke that rule.
* **Measuring it**: `compare` scores the run without lessons against the run
  with them on the same events, with a sign test on the events where the result
  changed. The `trade-verifier` skill walks through the week-by-week
  walk-forward that keeps the Verifier from reading ahead.

This is memory, not reinforcement learning: no weights change. The swarm gets
better only if reading its own past mistakes changes its calls, which is what
`compare` measures.

## Market data: `swingbot-data`

All price, news, filings and fundamentals reads go through one router in
`src/swingbot/data/`, modelled on the data layer of
[TradingAgents](https://github.com/TauricResearch/TradingAgents):

* **Sources are chains** set in `config/data_sources.toml`, tried left to right.
  Prices default to saved CSVs, then Yahoo (downloads are saved back to
  `data/raw/prices`). Switching to Dhan prices at paper trading is a config line
  once its adapter exists.
* **Every read has an as-of time.** Nothing that closed or was published later
  is served. A bare date means the end of that day; news at 11:00 does not get
  that day's close.
* **Gaps are said, not hidden.** A missing or failed source comes back as one
  line starting `NO_DATA`, `DATA_UNAVAILABLE`, `WITHHELD` or `NOT_CONFIGURED`,
  telling the model not to estimate. `WITHHELD` covers present-day-only data,
  such as Yahoo's ratios, asked for at a past date.
* **Fundamentals are point in time** where possible: the latest NSE results
  filing comes first, because it carries the exchange timestamp.

```bash
swingbot-data technicals RELIANCE                # now
swingbot-data news TCS --as-of 2026-08-03 --days 5
swingbot-data all INFY --as-of "2026-08-03 11:00"
```

The router feeds the agents in the two ways TradingAgents does:

* **Pre-fetch, for backtests.** `swingbot-briefs --with-technicals <prices dir>
  --with-context` adds technicals, the week's earlier news and filings, and the
  latest results filing to each event, cut to the moment it broke. Claude Code
  writes Market, News and Fundamentals analyst reports from that before the
  personas speak. It never calls `swingbot-data` during a backtest.
* **Tool calls, for paper and live research.** The `market-research` skill has
  Claude Code call `swingbot-data` itself, write the analyst reports, run the
  personas and the debate, and log the view to `runs/research.jsonl`.

## Running on real data

```bash
pip install -e ".[data]"
python scripts/fetch_prices.py --events data/my_events.csv \
    --start 2025-01-01 --end 2026-10-01 --out data/raw/prices
swingbot-backtest --events data/my_events.csv --prices data/raw/prices \
    --benchmark NIFTY50 --engine claude
```

Prices come from Yahoo Finance (`TICKER.NS`), split- and bonus-adjusted.

### Getting real news

Two fetchers build `data/raw/news/events.csv` for the stocks in
`data/universe/large_caps.csv` (edit that list freely; check symbols against NSE).
Both merge into the file, so running them again, or daily with `--start`
set to yesterday, only adds new items.

```bash
# Headlines from Indian outlets, via GDELT. Free, no key, open licence.
python scripts/fetch_gdelt_news.py --start 2026-07-01 --end 2026-10-15

# NSE exchange filings, timestamped to the second. Read --help first.
python scripts/fetch_nse_announcements.py --accept-nse-terms --start 2026-07-01 --end 2026-10-15
```

* **GDELT** stamps each article with the time it first saw it, usually within 15
  minutes of publication. Its DOC API only promises the last three months, so
  pull older windows soon. It matches body text, so the fetcher keeps only
  headlines that name the company. Cite "The GDELT Project" if you publish results.
* **NSE filings** use the exchange dissemination time. Routine paperwork
  (trading window, newspaper ads, ESOP allotments) is dropped unless you pass
  `--keep-routine`. NSE's terms of use forbid automated collection and
  "simulation" use, so this is for private research only: it runs slowly,
  writes under the git-ignored `data/raw/`, and its output must not be
  committed or shared.

Neither source has been run end to end yet; the parsing is tested against
sample records. Spot-check a few rows before trusting a run.

### Training cutoff

The `claude` and `recorded` engines, and `swingbot-briefs`, drop news
published before 1 July 2026 by default, because Claude's training data runs to
about June 2026 and it may remember what happened next. Change it with
`--cutoff YYYY-MM-DD` (or `--cutoff none`), and move `DEFAULT_LLM_CUTOFF` in
`src/swingbot/events.py` forward when the swarm moves to a newer model.
Synthetic sample events are never dropped.

## Risk engine: `swingbot-risk`

Plain, tested Python that the swarm cannot override, following the build
plan's risk rules. Limits live in `config/risk.toml`, which the bot only reads.
Nothing here talks to a broker.

```bash
swingbot-risk mood --out runs/risk/mood.json         # bullish, neutral or defensive market
swingbot-risk plan RELIANCE --stop atr:2 --target 2R --out runs/risk/plan.json
swingbot-risk odds runs/risk/plan.json --hold 10     # the Time Machine
swingbot-risk size runs/risk/plan.json --portfolio portfolio.json --mood runs/risk/mood.json --out runs/risk/order.json
swingbot-risk check runs/risk/order.json --portfolio portfolio.json --results-date 2026-11-14
swingbot-risk ticket runs/risk/order.json --out runs/risk/ticket.json   # paper only, never sent
swingbot-risk trail RELIANCE --entry-date 2026-10-20 --entry 1450 --initial-stop 1400 --stop 1400
swingbot-risk kill-switch                       # status; --reset --by NAME is for Harsha only
```

* **plan** sets the stop (ATR multiple, percent, price, or under a recent low)
  and the target (a multiple of the risk) from bars known at `--as-of`, rounded
  down to a 0.05 tick, and refuses a stop above the entry or tighter than half
  an ATR.
* **odds** (the Time Machine) asks how often the plan hits its target first,
  its stop first, or neither within `--hold` sessions. It replays the hold
  10,000 times, each session drawn at random from the stock's own last 500
  sessions as ratios to the previous close (seed 19, so results repeat). An
  open below the stop fills at the open, so gaps cost more than 1R; a session
  touching both sides counts as the stop. It also runs the same stop (in ATRs)
  and reward:risk from every past close on the real bars that followed: a base
  rate any signal has to beat. The verdict compares the target-first share
  with break-even, 1 / (1 + R). Costs are left out, and it is a reality check
  built from the past, not a prediction. It needs 120 sessions of history.
* **size** buys the most whole shares that fit every limit at once: 1% of
  equity lost at the stop, sized as if the open gaps 0.5% through it (R2, R9),
  15% of equity per position (R3), cash with no margin, and the room left under
  5% total open risk (R4). It names the limit that decided.
* **check** gives PASS or BLOCK with a line per rule: stop and target present
  (R1), risk per trade (R2), position cap (R3), 5 positions, 2 per sector and 5%
  open risk (R4), the weekly -5% kill switch, which latches until reset by hand
  (R5), the daily -2% pause (R6), close above the 200-day SMA and Rs 10 crore
  average daily value (R8), no entry within 2 sessions of results (R9), and the
  market mood (R11, below).
  Delivery (CNC) only, long only. A sell that only reduces a holding is never
  blocked. Every verdict is logged with a fingerprint of the exact order in
  `data/state/risk_verdicts.jsonl`.
* **ticket** writes the broker orders a PASSed order would become, for the
  paper journal and as the shape for the Dhan adapter: a DAY limit buy (CNC),
  then, once it fills, an OCO pair kept at the broker (target sell, and a stop
  that triggers at the stop with its limit 0.5% lower so a small gap still
  fills). It copies the risk engine's numbers and adds none. It refuses unless
  the latest logged verdict for the exact order (same fingerprint) is a PASS
  from `--as-of` (default today) and the kill switch is not latched. Nothing
  sends it: Dhan's security id and request fields are the adapter's job, to be
  checked in Dhan's sandbox.
* **trail** moves the stop to breakeven after +1R, then 2 x ATR under the
  highest close (R7). It never moves a stop down.
* **mood** (R11, new) calls the whole market bullish, neutral or defensive
  from five checks on free daily prices: the Nifty's 20-session trend, the
  Nifty against its 200-day average, the Nifty against Nifty FMCG (money
  hiding in staples), gold and USD/INR (a flight to safety), and India VIX.
  Two more bullish than defensive checks is bullish, and the reverse is
  defensive. Fewer than three available checks is "unknown", never neutral.
  Every series is cut to the closes known at `--as-of`. When the mood is
  defensive, `size` and `check --mood` cut the risk per trade to
  `defensive_risk_scale` of the usual 1% (0.5 by default; 1 turns it off, 0
  blocks new entries). Save the series with `scripts/fetch_prices.py --mood`;
  the Yahoo symbols are `^NSEI`, `^CNXFMCG`, `GOLDBEES.NS`, `INR=X` and
  `^INDIAVIX`. `swingbot-briefs --with-mood <prices dir>` shows the swarm the
  mood as of each story.

Sectors come from `data/universe/sectors.csv`; NSE holidays from
`config/nse_holidays.toml` (2026 only so far: check it against NSE's circular
and add each new year). Not yet covered: R1's 60-second stop confirmation and
R10 order rate limits, which belong to the broker adapter. The exit-planner,
Time Machine, sizing and gate structure borrow ideas from Jake Schincariol's
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT);
the code and the rules are this repo's own. The ticket step borrows the idea of
a final execution agent from The Swarm Corporation's
[AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) (MIT), with
plain code in place of an LLM. In Claude Code, the `risk-check`
skill runs these steps after `market-research`.

## Paper account: `swingbot-paper`

The account the paper-trading phase is judged on. Record each fill the paper
broker makes; the tool replays the ledger into cash, positions and a daily
equity curve.

```bash
swingbot-paper open --cash 1000000 --at 2026-11-02
swingbot-paper fill buy --order runs/risk/order.json --qty 30 --price 1451.5 --at "2026-11-03 09:20"
swingbot-paper stop RELIANCE 1450 --at "2026-11-06 15:40"
swingbot-paper fill sell RELIANCE --qty 30 --price 1530 --at "2026-11-10 10:05" --costs 61.2
swingbot-paper report --out runs/paper          # summary.json, equity.csv, trades.csv
swingbot-paper portfolio --out portfolio.json   # what swingbot-risk size/check read
```

* **The ledger** (`data/state/paper_ledger.jsonl`, git-ignored) is append-only
  JSON lines. A buy made with `--order` carries the order's stop, target and
  sector, and is stamped PASS only if `swingbot-risk check` passed that exact
  order (same fingerprint) on the fill's day. Charges default to 12.5 bps a
  side; pass `--costs` with the contract note's figure when you have it.
* **The report** gives equity and return, the return of the Nifty over the
  same sessions, the worst drawdown with its dates, closed trades with win
  rate, average R (against the initial stop), expectancy and profit factor,
  costs paid, and progress to the build plan's paper gate (6 months and 50
  trades, with no rule breaks). It exits 1 when any rule was broken.
* **Rule breaks** found on replay: a buy with no PASS, no stop or a stop at or
  above the fill, risk over 1% of equity or a position over 15% (with 2% slack
  for fill prices), more than 5 positions or 2 per sector, adding to a holding,
  a buy while the account sat 5% under the week's open, cash below zero, a sale
  of more than is held, and a stop moved down. It also notes any session that
  traded through a stop without a sell recorded, because the broker holds the
  stop and the ledger never invents its fill.
* **The portfolio file** takes today's open as the last close and Monday's open
  as the previous week's last close, since the ledger only has daily closes.

The replay idea, start from cash, apply trades in order, mark at each close and
flag rule breaks, comes from the challenge scoring in
[HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) (its README says MIT, but
the repository has no licence file); the code and rules are this repo's own.

## Daily paper trading loop: `swingbot-daily`

One run per NSE session day, chaining every tool above. Claude Code plays the
swarm with no API key, so the loop stops in the middle and waits for it. In
Claude Code, ask it to "run my day" (the `daily-paper-loop` skill), ideally
between 08:00 and 09:00 IST.

```bash
swingbot-daily start     # calendar, data refresh, paper broker settle, account, kill switch, mood, brief
# Claude Code plays the swarm on runs/daily/<date>/brief.md, writing views.jsonl there
swingbot-daily brief     # the next batch of today's news, until 0 are left
swingbot-daily finish    # signals, signal-to-order, risk gate, tickets, paper broker, report, Verifier
swingbot-daily status
```

* **Hours.** New entries need an NSE session day (weekends and the holidays
  in `config/nse_holidays.toml` are off) and a finish before the 15:30 close.
  Starting after 09:15 works but says the entries go in late. On other days
  the loop still settles exits, writes the report and runs the Verifier.
* **News window.** Everything published since the previous session's 09:15
  open, which matches the backtest's timing: that news is first tradeable at
  today's open. Older news had a session to be priced in.
* **Kill switch.** `start` checks the paper account against the week's open
  and latches the switch at a 5% loss itself, instead of waiting for an order
  to be checked. Latched means no swarm and no orders, only exits and the
  report. Nothing in the loop resets it.
* **The gate.** Each order goes through `swingbot-risk check` and
  `swingbot-risk ticket` exactly as a hand-made one would, so verdicts are
  logged and the switch can latch the same way. Each order is checked against
  the account plus the entries already PASSed that morning, so two orders
  can't share the same cash or risk budget.
* **The Verifier.** When trades have closed, `finish` adds them to
  `runs/memory.db` as paper trades, with the news and swarm call that opened
  them (linked through the order's fingerprint in
  `data/state/daily_entries.jsonl`), and writes `verify.md` for the
  `trade-verifier` skill.
* **Hooks.** The data refresh, the paper broker (settle and submit) and the
  signal-to-order step are named in `config/daily.toml` as `module:function`,
  with their contracts in `src/swingbot/daily/hooks.py`. Until each one is
  built, a stub stands in and the day's `summary.md` marks that step `STUB`,
  so a report never looks complete when it isn't. The refresh hook calls
  `swingbot.data.refresh` as soon as it is installed.

## Persona scorecard: `swingbot-scorecard`

Scores each persona's final stance, the judge and the final call as separate
calls on the same price moves the backtest measured:

```bash
swingbot-scorecard --run runs/<run>/results.csv runs/views.jsonl --by month --out runs/scorecard.csv
```

Per row: calls and no-calls, hit rate with its coin-flip p-value, average
return per call after costs with a 95% bootstrap range (seed 19), Brier score
of conviction, and rank IC of stance against the move. With four personas, a
judge and a final call, possibly by month, some row will look good by luck, so
every p-value also gets a Benjamini-Hochberg q-value and only q under 0.10 is
marked as beating luck. `benjamini_hochberg` and `bootstrap_ci` live in
`metrics.py` for other comparisons too. The scorecard only reports; it does
not change persona weights. The leaderboard idea is from AI-Trader.

## Things that can make this backtest lie

* **The LLM may remember the outcome.** For news inside the model's training
  data, Claude may know what happened next. The system prompt forbids using that
  memory but can't guarantee it. Treat results on news after the model's
  training cutoff as the honest test.
* **Wrong timestamps.** A headline stamped at publication by an aggregator that
  picked it up late can look like a pre-market call it wasn't.
* **Small samples.** Twenty events tell you almost nothing. Watch the p-value.
* **Costs and slippage.** 25 bps is a rough NSE delivery figure. Small caps cost more.
* **Survivorship.** Only testing stocks that are still listed today flatters results.

## Roadmap

Following the blueprint: Phase 2 (the Verifier and episodic memory) is in place
and needs a real walk-forward run to show whether it helps. The paper account
(`swingbot-paper`) is ready to record fills, and `swingbot-daily` runs a paper day end to end. Phase 3 adds paper trading through the DhanHQ API with
bracket orders, 1 to 2 percent position sizing and a weekly kill switch.
Live money comes only after months of paper trading.
