---
name: market-research
description: Research an NSE stock for a paper or live swing trade the way TradingAgents does, with Claude Code fetching data through swingbot-data, writing analyst reports, then running the persona swarm and Bull/Bear debate. Use when asked to research, analyse or give a view on a stock today. Not for backtests (use swarm-backtest).
---

# Market research, played by Claude Code

You run the TradingAgents pipeline yourself: analysts fetch data with a tool,
write reports, and everyone after them works only from those reports. The tool
is `swingbot-data`. Nothing here places an order; the output is a view for the
user and for the paper-trading journal.

This skill is for a decision being made now. For a historical test use the
`swarm-backtest` skill instead: there the brief does the fetching, cut to the
right moment, and you must not call `swingbot-data` at all.

## Rules

1. **Every number comes from a `swingbot-data` result.** Don't quote a price,
   ratio or date from memory or from a web page.
2. **Missing is missing.** A line starting `NO_DATA`, `WITHHELD`,
   `DATA_UNAVAILABLE` or `NOT_CONFIGURED` goes into the report as a gap. Never
   estimate around it, and don't read a gap as neutral.
3. **Keep `--as-of` the same for every call** in one piece of research. Leave it
   out for "now"; pass it when the user asks about a past moment, and then also
   don't use what you know happened after it.
4. Web search is fine for background (what a company does) but not for prices,
   results or news timing.

If `swingbot-data` is not on the PATH, run `pip install -e ".[data]"` first.
Prices come from saved CSVs, then Yahoo (downloads are saved back). News and
filings come from `data/raw/news/events.csv`, built by the fetchers in
`scripts/`. Yahoo and NSE are blocked from the cloud workspace, so on a cloud
session expect `DATA_UNAVAILABLE` for anything not already saved.

## Steps

1. **Market analyst.** Run `swingbot-data technicals <TICKER>` and, if you need
   the bars behind it, `swingbot-data prices <TICKER> --rows 60`. Write a
   report of four to six sentences on trend, momentum, volatility (quote ATR,
   it sets the stop distance) and volume.
2. **News analyst.** Run `swingbot-data news <TICKER> --days 7` and
   `swingbot-data filings <TICKER> --days 14`. Report what is new, when it
   broke (timestamps matter), and what the market may not have priced yet.
3. **Fundamentals analyst.** Run `swingbot-data fundamentals <TICKER>`. Report
   the latest results filing, or Yahoo's present-day ratios when that is the
   only source, and say which it was.
4. **Personas.** Play the four personas from `src/swingbot/swarm/personas.py`
   for two rounds, reading only the three reports: round 1 on their own, round 2
   reacting to each other. Each gives a stance (-1 to 1) and conviction (0 to 1).
5. **Bull/Bear debate and judge.** Bull and Bear researchers argue from the
   reports and persona views; the judge sides with the stronger case and gives
   a stance, conviction and one-line rationale.
6. **Report to the user**, in this order: the judge's call and conviction, the
   three analyst reports in one or two lines each, the persona spread, and every
   data gap. If the call is bullish or bearish, give the stop distance as a
   multiple of ATR14 so position size can follow the 1% risk rule. Say plainly
   that this is a paper-trading view, not an order.
7. **Save it** by appending one JSON line to `runs/research.jsonl`:
   `{"ticker", "as_of", "stance", "conviction", "rationale", "reports": {...}, "gaps": [...]}`.
   These lines are the start of the post-trade learning memory.
