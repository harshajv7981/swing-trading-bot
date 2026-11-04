---
name: trading-agents
description: Run TradingAgents' multi-agent decision (four analysts, Bull/Bear debate, Research Manager, Trader, three-way risk debate, Portfolio Manager) on an NSE stock today, played by Claude Code with swingbot-data, so it costs no API money. Logs every call for the forward paper test and scores past calls. Use when asked to "run trading agents", "what do the agents say about X", "run today's watchlist", or "how are the agent calls doing". Forward only; never places an order.
---

# TradingAgents, played by Claude Code

This is [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
(Apache-2.0) with its LLM calls replaced by you. Each role below is one of its
agents, in its graph order, with its brief adapted to this repo's data tools.
There is no API key and no per-call cost; it runs inside Harsha's Claude plan.

It is a **forward paper test**, fixed in
[docs/research/trading-agents-prereg.md](../../../docs/research/trading-agents-prereg.md).
The repo's earlier LLM swarm, built the same way, showed no edge in the Jul to
Oct 2026 walk-forward and was removed. This test exists to find out, on months
nobody has seen, whether this version does better. Treat its calls as data to
be scored, not advice.

## Rules

1. **Today only.** Never run it for a past date. A model may already know what
   happened after that date, which makes any past "result" worthless.
   `swingbot-agents record` refuses a past `as_of`.
2. **Every number comes from a tool result** (`swingbot-data`, `swingbot-risk
   mood`). No prices, ratios or dates from memory or the web. A line starting
   `NO_DATA`, `WITHHELD`, `DATA_UNAVAILABLE` or `NOT_CONFIGURED` is a gap: put
   it in the report as a gap, never estimate around it, never read it as neutral.
3. **Each role sees only what TradingAgents gives it.** Analysts see tool
   output; everyone after them sees only the reports and the debate so far. Keep
   each role's text separate, and don't let a later role quietly redo an
   earlier one's analysis.
4. **Record every run, whatever the answer.** Holds, Sells and unreadable runs
   (REVIEW) are logged too. Never rerun a stock to get a different call: the
   first call of the day stands, and `record` refuses a second one.
5. **The whole watchlist or nothing.** The test's calls are made on Fridays
   after the close, for every stock in the prereg's list, in its order.
   Skipping the ones that look dull is cherry-picking. A one-off stock on
   another day is fine when Harsha asks, but `record` logs it beside the test's
   calls, so say it is outside the watchlist schedule.
6. **Nothing here places an order.** A Buy or Overweight can go on to the
   `risk-check` skill for a sized paper ticket if Harsha asks. Never edit
   anything under `config/`.

## Before the first stock of the day

```bash
swingbot-refresh --only prices news --tickers <the watchlist> NIFTY50 NIFTYFMCG GOLDBEES USDINR INDIAVIX
swingbot-risk mood --out runs/risk/mood.json
```

Run after the close (16:00 IST or later) so today's bar is in. If a stock's
technicals still show an older last close, say so in the market report: it is
a gap, not today's picture.

## Steps, for one stock (TICKER, no `.NS`)

Use one `as_of` for the whole run: now, as `YYYY-MM-DD HH:MM` IST.

**1. Market Analyst.** `swingbot-data technicals TICKER` and
`swingbot-data prices TICKER --rows 60`. Pick the indicators that matter here
(trend: EMA10, SMA50, SMA200; momentum: RSI, MACD; volatility: ATR, Bollinger;
volume: VWMA) without repeating each other, and write a detailed report of the
trend, momentum, volatility (quote ATR14: it sets the stop) and volume, ending
in a short Markdown table of the key points. Report what the data shows; you
don't decide the trade here.

**2. Sentiment Analyst.** TradingAgents reads Reddit and StockTwits; this repo
has neither, so write `Social sentiment: NOT_CONFIGURED` as a gap. From
`swingbot-data news TICKER --days 7`, read the tone of the coverage: separate
events (a contract, a downgrade) from opinion, say how many items there were,
and lower your confidence plainly when there are few.

**3. News Analyst.** `swingbot-data news TICKER --days 7` and
`swingbot-data filings TICKER --days 14`, plus the market mood from
`runs/risk/mood.json` (bullish, neutral, defensive or unknown, and any check
that was unavailable). What is new, when it broke (timestamps matter), what
the market may not have priced yet, and the macro backdrop the mood shows.
End with a Markdown table.

**4. Fundamentals Analyst.** `swingbot-data fundamentals TICKER`. The latest
results filing, or Yahoo's present-day ratios when that is the only source;
say which it was. End with a Markdown table.

**5. Bull Researcher, then Bear Researcher** (one round each, TradingAgents'
default). The Bull builds an evidence-based case for owning the stock from
the four reports: growth, competitive position, positive signals, and answers
to the bear's points. The Bear then argues against: risks, weak spots,
negative signals, and direct answers to the bull's points. Each speaks
conversationally and cites the reports, not new data.

**6. Research Manager.** Judge the debate and give an investment plan for the
trader: a rating on the 5-tier scale (Buy, Overweight, Hold, Underweight,
Sell), the reasons, and the strategy. Don't default to Hold because both sides
made points: pick the stronger case.

**7. Trader.** Turn the plan into a proposal. Start with `Action: Buy`, `Hold`
or `Sell` on its own line (Overweight counts as Buy, Underweight as Sell), then
the reasoning, then the entry and stop as rupee prices from the market report
(last close, support and resistance, ATR), never a percentage. Give the stop
distance as a multiple of ATR14.

**8. Risk debate: Aggressive, Conservative, Neutral analysts** (one round each,
in that order). Each reads the four reports and the trader's proposal. The
Aggressive analyst argues for taking the upside and challenges caution; the
Conservative analyst protects capital and presses on what could go wrong; the
Neutral analyst weighs both and says where each overreaches. Each answers the
others' last points.

**9. Portfolio Manager.** First run `swingbot-agents score --ticker TICKER`:
its settled lines are this stock's past calls and what followed (TradingAgents
gives its Portfolio Manager these lessons). Then synthesise the risk debate
into the final decision. The **first line must be `Rating: X`** on the 5-tier
scale, then the rationale, the entry and stop, and what would change the call.
If you can't commit to one rating, the rating is `REVIEW`.

**10. Record it.** Write the decision to
`runs/trading-agents/<YYYY-MM-DD>/TICKER.json`:

```json
{"ticker": "TICKER", "as_of": "YYYY-MM-DD HH:MM", "rating": "Overweight",
 "rationale": "one or two lines from the Portfolio Manager",
 "action": "Buy", "entry": 0.0, "stop": 0.0, "atr14": 0.0,
 "reports": {"market": "...", "sentiment": "...", "news": "...", "fundamentals": "...",
             "debate": "...", "research_plan": "...", "trader": "...", "risk_debate": "...",
             "final": "..."},
 "gaps": ["Social sentiment: NOT_CONFIGURED"]}
```

then `swingbot-agents record runs/trading-agents/<YYYY-MM-DD>/TICKER.json`. A
refusal is final: tell the user what it printed and don't rewrite the file to
get round it.

## What to tell the user

Per stock: the final rating and the one-line rationale, the trader's entry and
stop (and the stop in ATRs), the four analyst reports in one line each, and
every data gap. For a watchlist day, one line per stock, then the count of each
rating. Say plainly that these are paper calls in a forward test, not orders
or advice.

## Scoring

`swingbot-agents score` settles each call 5 sessions after the first close
following it, against the Nifty 50, as TradingAgents does: the scored return is
the stock's return minus the Nifty's, signed by the call (Buy and Overweight
+1, Sell and Underweight -1; Holds and REVIEW are logged but not scored). Give
the summary line as printed. The prereg says when there are enough calls to
judge; before that, say the numbers are too few to mean anything.
