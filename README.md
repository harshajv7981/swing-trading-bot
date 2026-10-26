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
swingbot-risk plan RELIANCE --stop atr:2 --target 2R --out runs/risk/plan.json
swingbot-risk odds runs/risk/plan.json --hold 10     # the Time Machine
swingbot-risk size runs/risk/plan.json --portfolio portfolio.json --out runs/risk/order.json
swingbot-risk check runs/risk/order.json --portfolio portfolio.json --results-date 2026-11-14
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
  average daily value (R8), and no entry within 2 sessions of results (R9).
  Delivery (CNC) only, long only. A sell that only reduces a holding is never
  blocked. Every verdict is logged with a fingerprint of the exact order in
  `data/state/risk_verdicts.jsonl`.
* **trail** moves the stop to breakeven after +1R, then 2 x ATR under the
  highest close (R7). It never moves a stop down.

Sectors come from `data/universe/sectors.csv`; NSE holidays from
`config/nse_holidays.toml` (2026 only so far: check it against NSE's circular
and add each new year). Not yet covered: R1's 60-second stop confirmation and
R10 order rate limits, which belong to the broker adapter. The exit-planner,
Time Machine, sizing and gate structure borrow ideas from Jake Schincariol's
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT);
the code and the rules are this repo's own. In Claude Code, the `risk-check`
skill runs these steps after `market-research`.

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
and needs a real walk-forward run to show whether it helps. Phase 3 adds paper trading through the DhanHQ API with
bracket orders, 1 to 2 percent position sizing and a weekly kill switch.
Live money comes only after months of paper trading.
