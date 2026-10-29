# swing-trading-bot

Tools for swing trading Indian equities: market data, hard risk rules, a paper
account and broker, a Dhan sandbox adapter, a dashboard, and the mid-cap
momentum paper trial. Paper trading only: nothing here places a real order.

The LLM news swarm that this repo started with (personas, debate, briefs,
backtest, scorecard, learning memory, daily loop, walk-forward, phone alerts)
showed no edge in the Jul–Oct 2026 walk-forward and was removed. It is still in
git history at commit `c1eef8d` (main on 29 Oct 2026, before the removal);
bring a piece back with, for example, `git checkout c1eef8d -- src/swingbot/swarm`.

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

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

## Running on real data

```bash
pip install -e ".[data]"
python scripts/fetch_prices.py --tickers TCS INFY \
    --start 2025-01-01 --end 2026-10-01 --out data/raw/prices
```

Prices come from Yahoo Finance (`TICKER.NS`), split- and bonus-adjusted.

### Daily refresh

Once you have data, one command keeps it current. Run it on your own machine
after the close (16:00 IST or later); the cloud workspace can't reach Yahoo,
GDELT or NSE.

```bash
swingbot-refresh                      # prices for the universe, Nifty and the mood series; GDELT news
swingbot-refresh --accept-nse-terms   # also NSE filings (see the terms note below)
swingbot-refresh --only prices --tickers TCS INFY
```

It writes where the data router reads (`config/data_sources.toml`), so
`swingbot-data`, the briefs and the market mood pick up the new rows at once.
Prices are fetched only from the last saved bar on, with a week of overlap: if
Yahoo's adjusted closes for those days have moved (a split, bonus or dividend),
the whole file is downloaded again rather than mixing old and new adjustments.
A session that hasn't closed is never saved. News and filings resume from the
last successful day per stock, kept in `data/raw/refresh_state.json`. Running it
twice does nothing new, a lock stops two runs overlapping, and one stock
failing doesn't stop the rest (exit code 1, and the next run catches up).
Settings live under `[refresh]` in the config; the daily loop calls
`swingbot.data.refresh.run()`.

### Getting real news

Two fetchers build `data/raw/news/events.csv` for the stocks in
`data/universe/nifty100.csv`, the Nifty 100 (checked against NSE's own list on 27 Oct 2026,
after the September rebalance: BSE, POWERINDIA, POLYCAB, VAML and IDEA joined;
INDHOTEL, LODHA, RECLTD, SHREECEM and UNITDSPR left and are still listed), and
`data/universe/midcap150.csv`, the Nifty Midcap 150 (from NSE's list on 29 Oct 2026;
the five stocks already in nifty100.csv are listed there only, so 145 rows). The
`universe` setting under `[refresh]` and every fetcher's `--universe` take one
file or several. NSE rebalances both indices in
March and September: run `python scripts/update_universe.py` (and
`--index midcap150`) on your own
machine afterwards to see what joined or left, and `--write` to add the
joiners with a sector each (`data/universe/sectors.csv`, which R4 reads).
Leavers are kept until you drop them by hand, since one may still be held.
Midcaps trade less: R8's liquidity floor (`min_traded_value_cr` in
config/risk.toml, ₹10 crore a day) applies to them too; raise it by hand if
you want a wider margin. With about 250 stocks a full GDELT DOC refresh takes
longer still (one request every 5 seconds, about 20 minutes); the nightly
GitHub artifact (`news_source = "github"`) avoids that.
Both merge into the file, so running them again, or daily with `--start`
set to yesterday, only adds new items.

```bash
# Headlines from Indian outlets, via GDELT. Free, no key, open licence.
python scripts/fetch_gdelt_news.py --start 2026-07-01 --end 2026-10-15

# The same headlines from GDELT's bulk files, when the API above rate limits you
# (it blocks an IP for a day or more after heavy use) or for windows older than
# three months. No request limit, but it downloads about 0.5 to 1 GB a day of news.
python scripts/fetch_gdelt_bulk.py --start 2026-07-01 --end 2026-10-27
# Or let GitHub do the download (free on a public repo): Actions > "Fetch data" >
# Run workflow, then fetch the few-MB result of news and prices:
gh run download --repo harshajv7981/swing-trading-bot --name swingbot-data --dir .
# After that backfill, the same workflow runs by itself at 22:00 and 07:30 IST
# for yesterday and today (news only), and the daily refresh merges that file
# instead of calling GDELT from your machine (needs `gh`, signed in):
swingbot-refresh --news-source github   # or news_source = "github" under [refresh]

# NSE exchange filings, timestamped to the second. Read --help first.
python scripts/fetch_nse_announcements.py --accept-nse-terms --start 2026-07-01 --end 2026-10-15
```

* **GDELT** stamps each article with the time it first saw it, usually within 15
  minutes of publication. Its DOC API only promises the last three months, so
  pull older windows soon. It matches body text, so the fetcher keeps only
  headlines that name the company. Cite "The GDELT Project" if you publish results.
  The bulk fetcher reads the Global Knowledge Graph files GDELT writes every 15
  minutes and keeps titles from Indian outlets (`--any-source` for all) that name
  a company. Its rows carry the batch time, the same ids as the API's, and the
  run resumes by day from `events.csv.gkg_days.json`. It skips names that belong
  to other companies (L&T Technology Services, the demerged Vedanta units, SBI
  Mutual Fund: `OTHER_COMPANIES` in `swingbot/news.py`), job notices and fund
  pages, and pages whose URL dates them more than a week before GDELT saw them.
  Market roundups that name several stocks are kept, one row per stock.
* **The nightly GitHub run** uploads a `swingbot-news-daily` artifact (kept 14
  days). With `--news-source github` the refresh downloads the newest one with
  the GitHub CLI and merges it by id, as one `news *` line in its report; it
  reports a failure if `gh` is missing or signed out, or if the newest file is
  more than two days old. GitHub disables a scheduled workflow after 60 days
  without activity in the repo, so if that happens, re-enable "Fetch data" from
  the Actions tab. The repo is public, so its artifacts are too: GDELT
  headlines and Yahoo prices only. NSE filings must never go through Actions.
* **NSE filings** use the exchange dissemination time. Routine paperwork
  (trading window, newspaper ads, ESOP allotments) is dropped unless you pass
  `--keep-routine`. NSE's terms of use forbid automated collection and
  "simulation" use, so this is for private research only: it runs slowly,
  writes under the git-ignored `data/raw/`, and its output must not be
  committed or shared.

Neither source has been run end to end yet; the parsing is tested against
sample records. Spot-check a few rows before trusting a run.

## Risk engine: `swingbot-risk`

Plain, tested Python that the swarm cannot override, following the build
plan's risk rules. Limits live in `config/risk.toml`, which the bot only reads.
Nothing here talks to a broker.

```bash
swingbot-risk mood --out runs/risk/mood.json         # bullish, neutral or defensive market
swingbot-risk plan RELIANCE --stop atr:2 --target 2R --out runs/risk/plan.json
swingbot-risk odds runs/risk/plan.json --hold 10     # the Time Machine
swingbot-risk size runs/risk/plan.json --portfolio portfolio.json --mood runs/risk/mood.json --out runs/risk/order.json
swingbot-risk results                                # next results dates from board meeting notices (R9)
swingbot-risk check runs/risk/order.json --portfolio portfolio.json
swingbot-risk ticket runs/risk/order.json --out runs/risk/ticket.json   # paper only, never sent
swingbot-risk trail RELIANCE --entry-date 2026-10-20 --entry 1450 --initial-stop 1400 --stop 1400
swingbot-risk kill-switch --portfolio portfolio.json   # status, latching it on a -5% week
swingbot-risk kill-switch --reset --by Harsha   # Harsha only, typed at a terminal
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
  market mood (R12, below).
* **Results dates (R9)** come from the companies' own NSE "Board Meeting
  Intimation" filings in the events file ("board meeting will be held on
  14-Nov-2026 ... to consider the financial results"), so they arrive with
  `swingbot-refresh --accept-nse-terms` at no extra cost. A notice counts only
  once it was published, and a later notice for the same company (a moved
  date) wins. `check`, `orders` and the daily loop read them by themselves;
  `--results-date` still overrides one order. For a company whose notices
  weren't collected, type `symbol,date` lines into `data/state/results_dates.csv`
  (several lines per company is fine; each date counts until it has passed, and
  the sooner of a typed date and a notice wins). No notice means the date is unknown: R9 warns, and
  once dates are flowing, set `require_results_date = true` in
  `config/risk.toml` so unknown blocks.
  Delivery (CNC) only, long only. A sell that only reduces a holding is never
  blocked. Every verdict is logged with a fingerprint of the exact order in
  `data/state/risk_verdicts.jsonl`.
* **ticket** writes the broker orders a PASSed order would become, for the
  paper journal and as the shape for the Dhan adapter: a DAY limit buy (CNC),
  then, from the next trading day, an OCO pair kept at the broker, both legs
  market (a target sell, and a stop-loss market sell at the stop). A
  stop-limit would miss a gap down below its limit; a stop-loss market sells
  at about the open instead. It copies the risk engine's numbers and adds none. It refuses unless
  the latest logged verdict for the exact order (same fingerprint) is a PASS
  from `--as-of` (default today) and the kill switch is not latched. Nothing
  sends it: Dhan's security id and request fields are the adapter's job, to be
  checked in Dhan's sandbox.
* **kill-switch** latches on the drawdown itself, not only on an entry check:
  any `check` (a sell too), `kill-switch --portfolio`, and every
  `swingbot-paper report` or `portfolio`, which scans each close of the paper
  account against its week's open. A reset asks Harsha to type RESET at a
  terminal and refuses inside Claude Code (`CLAUDECODE` set) or with no
  terminal; it is logged with who reset it, and that week's breach does not
  latch it again (the gate still blocks entries while the week is 5% down).
* **trail** moves the stop to breakeven after +1R, then 2 x ATR under the
  highest close (R7). It never moves a stop down.
* **mood** (R12, new) calls the whole market bullish, neutral or defensive
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
  `^INDIAVIX`.

Sectors come from `data/universe/sectors.csv`; NSE holidays from
`config/nse_holidays.toml` (2026 only so far: add each new year from NSE's
circular). R1's
60-second stop confirmation, R10 order rate limits and R11, the live-mode flag
(off by default), belong to the Dhan adapter (`swingbot-dhan`, below); the
market mood is R12. The exit-planner,
Time Machine, sizing and gate structure borrow ideas from Jake Schincariol's
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT);
the code and the rules are this repo's own. The ticket step borrows the idea of
a final execution agent from The Swarm Corporation's
[AutoHedge](https://github.com/The-Swarm-Corporation/AutoHedge) (MIT), with
plain code in place of an LLM. In Claude Code, the `risk-check`
skill runs these steps.

### From swarm calls to tickets: `swingbot-risk orders`

The steps above, for a whole file of swarm calls at once. Each call is the
ReportAgent's JSON (`ticker`, `sentiment`, `confidence`, `time_horizon`, and
optionally `as_of`, `event_id`, `score`, `rationale`), as JSONL, a JSON list
or CSV.

```bash
swingbot-risk mood --out runs/risk/mood.json
swingbot-risk orders runs/signals.jsonl --portfolio portfolio.json --mood runs/risk/mood.json \
    --no-write                                     # dry run; R9 dates come from the notices (see above)
```

1. **The call.** Bullish only: the bot is long-only CNC, so a bearish call is a
   pass, never a short, and neutral is no trade. The horizon must be 3 to 20
   sessions (shorter cannot cover delivery costs), the call made after the
   open of the last session before the entry (news drift lasts a day or two),
   and the confidence at least 0.60 (provisional until the swarm's calls are
   scored; a defensive mood can get its own bar, and the gate halves the size).
2. **The batch.** One call per stock (the most confident), best calls first,
   at most 3 tickets per run.
3. **Technical overlay**, from bars known at `--as-of`: close above the
   200-day SMA, RSI14 at most 80, close at most 3 ATRs above EMA10. These skip
   a call before it is sized, so a stretched chart never reaches the gate.
4. **Plan, size, gate**: `atr:2.5` stop and `2R` target, sized and checked
   exactly as `plan`, `size` and `check` do it. Every verdict goes in
   `data/state/risk_verdicts.jsonl` and a weekly breach latches the kill
   switch. An order under Rs 25,000 is skipped (Dhan's per-sale DP charge
   would be too large a share of it).
5. **Ticket** for each PASS, plus a time exit from the horizon: 72h means sell
   at the close of the third session, counting the entry day, as in the
   backtest. The paper broker and the Dhan adapter both carry it out.

Calls in one run are sized one after another against the same account, so
each ticket's cash and open risk count against the next. Output goes to
`runs/orders/<date>/`: `decisions.json` (every call, where it stopped and
why) and a `TICKER.order.json` and `TICKER.ticket.json` per ticket. The order
file is what `swingbot-paper fill buy --order` reads. The filters live in
`config/signals.toml`, and `docs/research/signal-thresholds.md` gives the
evidence for each value (a test on 94 NSE large caps, 2010-2026, and the
published work it rests on). They can only skip a call and never loosen a limit in
`config/risk.toml`. Nothing is sent anywhere.

For the daily loop, `swingbot.risk.signals:daily_hook` is the
`signal_to_order` hook: the same steps up to the size, returning orders for
the loop to gate and ticket itself, with every call's decision in the day's
`signal_decisions.json`.

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
  a buy while the account sat 5% under the week's open or 2% under the day's, cash below zero, a sale
  of more than is held, and a stop moved down. It also notes any session that
  traded through a stop without a sell recorded, because the broker holds the
  stop and the ledger never invents its fill.
* **The portfolio file** takes today's open as the close of the session before
  the `--as-of` day and Monday's open as the previous week's last close, since
  the ledger only has daily closes. So a 2% fall from yesterday's close pauses
  entries (R6), and a 5% fall in the week latches the kill switch (R5).
* **Splits and bonuses**: the saved prices are adjusted, so after a 1:1 bonus
  every older bar is halved. Each split or bonus is listed in
  `data/state/corporate_actions.csv` (`symbol,ex_date,ratio,source,noted_at`;
  ratio = shares after per share before, so a 1:1 bonus is 2). The replay
  reads every older ledger line as `qty x ratio` at `price / ratio`, and the
  paper broker moves its live orders the same way on the ex-date. Profit, loss
  and R come out the same, and there is no fake drawdown or fake stop hit.
  `swingbot-refresh` adds the line when Yahoo re-adjusts a stock by a
  split-sized ratio. You can type one in by hand too. Dividends are not
  tracked: they shift old marks by a percent or two and are not credited as
  cash.

The replay idea, start from cash, apply trades in order, mark at each close and
flag rule breaks, comes from the challenge scoring in
[HKUDS/AI-Trader](https://github.com/HKUDS/AI-Trader) (its README says MIT, but
the repository has no licence file); the code and rules are this repo's own.

### The paper broker

Instead of typing fills in, hand the paper broker the ticket and let it fill
from daily bars. It writes every fill to the same ledger.

```bash
swingbot-risk ticket runs/risk/order.json --out runs/risk/ticket.json
swingbot-paper submit runs/risk/ticket.json --at "2026-11-02 18:00"   # live for the next session only
swingbot-paper settle --as-of "2026-11-03 18:00" --trail             # after each day's prices are in
swingbot-paper orders                                                # the book
swingbot-paper close RELIANCE                                        # sell at the next open
swingbot-paper cancel P00003                                         # an entry that has not filled
```

* **Entry**, a DAY limit buy: fills at the open if the stock opens at or under
  the limit, else at the limit if the low trades *under* it (a touch is not
  enough, there is a queue), else it lapses at the close. An entry placed
  after 09:08 on its own session day (the pre-open order entry has closed) can't
  get the open: it fills at the limit only if the day traded under it. A close
  asked for after 09:08 sells at the next session's open.
* **Entry-day stop.** Dhan takes no Forever Order on shares bought today, so
  the entry session is covered by a DAY stop-loss market sell placed once the
  buy fills. A low at or under the stop after the fill sells at the stop (at
  the open, if the stock was bought at an open already under it). There is no
  target leg that day.
* **Exits**, one OCO from the session after the entry, both legs MARKET as the
  ticket writes them. An open at or under the stop sells at the open (a gap: worse than 1R, but
  taken); an open at or over the target sells at the open; else the stop sells
  at the stop when the low reaches it, and the target at the target when the
  high does. When one bar reaches both, the stop is assumed to have come first.
  Every market sell gives up 5 bps of slippage.
* **Time exit**: a ticket from signal-to-order carries `hold.exit_by`. If
  neither leg has filled by that session's close, the position sells at the
  close (less slippage), as the backtest does. The Dhan adapter does the same
  with a market sell from 15:15 on that session.
* **Trailing**: with `--trail`, R7 runs after each close and moves the stop for
  the next session; `swingbot-paper stop` moves it by hand. Stops never go down.
* **Times**: 09:15 for fills at the open, 15:30 for fills during the session,
  whose real time a daily bar does not have.
* **The book** (`data/state/paper_broker.json`, git-ignored) is saved only
  after the fills are in the ledger. If a stock's prices are behind, the whole
  book waits rather than letting an order lapse unseen. It refuses a ticket not
  marked paper_only, one whose PASS is older than the session before the
  entry, a second order in a stock it already holds, and any entry while the
  kill switch is latched.
* **Same interface as Dhan**: `swingbot.broker` defines `submit`, `cancel`,
  `close`, `modify_stop`, `sync` and `open_orders`, and `DhanBroker` (next)
  has the same methods.
* **In the daily loop**: `swingbot.broker.daily:settle` and
  `swingbot.broker.daily:submit` are the loop's `broker_settle` and
  `broker_submit` hooks (book in `<state_dir>/paper_broker.json`, R7 trailing on).

### Dhan's sandbox: `swingbot-dhan`

```
export SWINGBOT_DHAN_CLIENT_ID=...  SWINGBOT_DHAN_TOKEN=...   # DhanHQ DevPortal, Sandbox tab
swingbot-dhan check                 # mode, URL and the sandbox account's funds
swingbot-dhan scrips --refresh      # Dhan's scrip master -> data/state/dhan_scrips.csv
swingbot-dhan submit ticket.json    # a PASSed ticket from swingbot-risk ticket
swingbot-dhan sync                  # after 09:15: fills, then the stop for yesterday's buys
swingbot-dhan sync --at-open        # run it before the open and it waits for 09:16
swingbot-dhan book
```

The adapter sends the ticket's entry as a CNC limit buy (an after-market
order when placed outside hours) and, from the next session, the exits as a
Dhan Forever OCO with both legs at market. Fills come from Dhan's trade
history with the contract note's charges as costs, into their own ledger
(`data/state/dhan_sandbox_ledger.jsonl`), because the sandbox fills everything
at a fixed price and resets its cash daily: it tests the plumbing, not the
P&L.

* **R1, 60 seconds.** After placing the OCO it reads Dhan's Forever book back
  until the stop leg is accepted at the right trigger. If that has not
  happened in 60 seconds it cancels and tries once more; if the second try
  fails the position is sold at market (`flatten_unprotected` in
  `config/broker.toml`). A stop that later disappears at Dhan while the shares
  are still held is placed again on the next sync. A market sell Dhan rejects
  (a lower circuit) or lets lapse is dropped on the next sync, so the shares
  get a stop again instead of sitting with none.
* **Entry day.** The Forever OCO can only go in from the next session, so once
  the buy has filled, a sync during market hours places a DAY stop-loss market
  sell (CNC) at the stop (`entry_day_stop` in `config/broker.toml`). Dhan
  treats selling shares bought the same day as intraday. Whether the sandbox
  takes it is still to be checked; a refusal is reported, and the OCO still
  goes in the next day.
* **Every session day after 09:15.** Dhan takes a Forever Order only while the
  market is open, and the daily loop runs before it. Schedule `swingbot-dhan
  sync --at-open` for each session morning (cron or launchd on the Mac); started
  before 09:16 it waits, then places and confirms the stops.
* **Splits and bonuses.** The exchange cancels pending orders on the ex-date.
  The next sync moves the holding's shares, stop and target onto the new terms
  from `data/state/corporate_actions.csv` (the file the paper broker reads) and
  places the stop again. Bonus shares reach the demat account a few days after
  the ex-date, so the stop first covers what Dhan holds and is placed again for
  all the shares once they arrive.
* **R10, rate limits.** Every call is counted in `data/state/dhan_rate.json`
  against Dhan's limits (10 order calls a second, 250 a minute, 1,000 an hour,
  7,000 a day; 5 data calls a second). A busy second or minute is waited out;
  past the hour or day limit nothing more is sent. Dhan allows 25 changes to
  one order, so `modify_stop` stops there.
* **R11, live mode.** Sandbox unless `config/broker.toml` says `mode = "live"`
  and the environment has `SWINGBOT_DHAN_LIVE=yes`. Even then this version
  only reads (funds, trades): every order call is refused until the paper
  phase is passed. Live calls also need Dhan's static IP whitelisting and a
  token that lasts one day.

Still to check in the sandbox itself: which Forever Order fields Dhan reads
as the stop leg (R1's read-back catches a swap and refuses it), and the trade
history's field names. The daily loop still runs on the paper broker; the
sandbox runs beside it by hand.

## Dashboard: `swingbot-dashboard`

```
swingbot-dashboard --open                   # runs/dashboard.html for the paper account
```

One self-contained HTML page (no scripts or web fonts, works offline, light
and dark): equity against the Nifty, the week's drawdown next to the kill
switch level, open risk, the paper gate's progress, open positions with their
stops (a missing stop shows in red), the paper broker's live orders, closed
trades with R. It only
reads files: it never latches the kill switch or writes the ledger, and
prices come from the saved CSVs. Re-run it to refresh.

## Mid-cap momentum paper trial: `swingbot-momentum`

A monthly paper portfolio of the one rule that passed the momentum research:
the 20 mid caps (ranks 101–250 by traded value) with the best 6-1 month
return, rebalanced at each month's last session. No model calls, no orders.
Rules and how it is judged: [docs/research/momentum-paper-trial.md](docs/research/momentum-paper-trial.md).

```bash
# once: a shallow copy of eod2_data's daily folder (about 140 MB download), kept out of git
git clone --depth 1 --filter=blob:none --sparse https://github.com/BennyThadikaran/eod2_data data/raw/eod2_data
git -C data/raw/eod2_data sparse-checkout set daily

swingbot-momentum update --data data/raw/eod2_data/daily --pull   # safe to run daily
swingbot-momentum report
```

`update` books every finished month-end the ledger lacks, using only prices up
to that day. It does nothing until the data covers a month-end, and eod2_data
syncs about weekly. The ledger is `data/state/momentum_trial.json`. The report
goes to `runs/momentum-trial/report.md`. Both are git-ignored. eod2_data is
NSE-derived, so keep it, the ledger and the report off GitHub Actions.

## Roadmap

The mid-cap momentum paper trial runs from November 2026 and is judged in
October 2027. The risk rules, paper broker and Dhan adapter stay for whichever
strategy goes live; Dhan still needs the sandbox checks before real orders.
