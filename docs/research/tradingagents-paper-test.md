# TradingAgents paper test: rules fixed before the first run

**Change on 2026-11-04, before any decision was made:** Harsha has no budget
for an API key, so the decisions are made inside Claude Code on his plan by the
`tradingagents-weekly` skill, not by the TradingAgents graph on the Haiku API.
The data tools, roles, rating scale and scoring stay the same. The
differences are listed under "Engine" below. The API runner
(`swingbot-agents run`) is kept but is not used for this test.

Written 2026-11-02, before any decision was made. Harsha asked to test
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
with paper money and chose a forward weekly test on Claude Haiku 5.5. Nothing
here places an order, and the mid-cap momentum paper trial is not touched.

## Why forward and not a backtest

A language model may have read news and prices from the months it was trained
on, so a backtest over those months can be scored on what it remembers. Every
decision here is made on a Friday that had not happened when the model was
trained, so that can't occur. The price is time: about 13 weeks to collect
~260 calls.

## The setup (fixed)

- **Framework:** TradingAgents v0.7.1, pinned in `pyproject.toml` (`pip install -e '.[agents]'`).
- **Engine:** Claude Code running the `tradingagents-weekly` skill
  (`.claude/skills/tradingagents-weekly/SKILL.md`), on whatever model the plan
  serves, recorded with each decision. `swingbot-agents brief` fetches each
  stock's data with TradingAgents' own tools (verified market snapshot,
  fundamentals, a week of company news and India market news), cut at the
  decision date, with no model calls. Claude then writes every role in order:
  the market, news and fundamentals analysts, the bull and the bear, the
  research manager, the trader, the three risk analysts and the portfolio
  manager. It follows TradingAgents' rating scale and its rule to commit rather
  than default to Hold. Differences from the graph: one model writes every
  role in one pass, so the roles are less independent; the analysts read a
  fixed data pack instead of choosing their own tool calls; and the memory is
  the stock's own earlier ratings and 5-day outcomes, shown in its brief.
- **Analysts:** market, news, fundamentals. The social analyst is left out: it
  reads StockTwits and Reddit, which barely cover NSE stocks. Global news
  queries are switched from US topics to India ones (Nifty, RBI, FPI flows,
  rupee and oil).
- **Stocks:** 20 from the Nifty Midcap 150 list in `data/universe/midcap150.csv`,
  drawn with `random.Random(20261102).sample(sorted(symbols), 20)`:
  ASTERDM, AUBANK, BAJAJHFL, CRISIL, FEDERALBNK, GODFRYPHLP, GODREJPROP, GVT&D,
  LICHSGFIN, MFSL, NH, NHPC, NMDC, PATANJALI, PIRAMALFIN, SRF, SWIGGY,
  UNOMINDA, WAAREEENER, YESBANK. A test checks the draw.
- **When:** one decision per stock each week from the week of 6 Nov 2026,
  made on the Mac after the NSE close on the decision day, normally Friday.
  The decision date is the day the brief is made. Decision dates must be at
  least 7 days apart, so the 5-session holds never overlap; `brief` enforces
  this. Data comes from Yahoo, as TradingAgents ships it; the cloud machine
  can't reach Yahoo.

**Data fixes on 2026-11-06, after the first week's calls were recorded:**
in the 6 Nov briefs, Yahoo's company news failed for all 20 stocks, and
GVT&D had no prices at all (TradingAgents' price cache refuses a ticker with
"&"). From 13 Nov, the brief falls back to the repo's own news file (GDELT
headlines) when Yahoo serves nothing, and GVT&D's prices load. The stocks,
roles, ratings and scoring are unchanged; the 6 Nov calls stand as recorded.

## How a call is scored

- **Hold window:** from the first NSE close after the Friday to the fifth close
  after that, the rule TradingAgents' own backtest uses. Weeks do not overlap.
- **Long calls:** Buy and Overweight. Hold, Underweight and Sell mean no
  position (NSE cash shares can't be shorted for a week). An unreadable rating
  means no position and is counted.
- **Paper book:** equal money in every long call each week, cash when there are
  none, back to equal weight each week, 0.20% a side on every rupee traded
  (the momentum trial's cost). Prices from eod2_data (`swingbot-agents score`).
- **Comparisons:** the same 20 stocks held in equal weight (did the picking add
  anything?), and the Nifty Midcap 150 index.

## Pass bar (judged after 13 scored weeks, around early February 2027)

Both must hold:
1. Week by week, the long calls' average 5-day return minus that week's
   equal-weight return of all 20 is positive with t ≥ 2.0 over the weeks with a
   long call.
2. The paper book, after costs, ends ahead of the same 20 in equal weight.

If either fails, the recommendation is to stop TradingAgents and spend nothing
more on it. If both pass, the next step is a longer run before any real money,
since 13 weeks is a short sample.

## Cost

No API spend: the skill runs on Harsha's Claude plan and uses its usage
allowance. If a weekly run of 20 stocks uses too much of the plan, the fix is
shorter notes per role, not fewer stocks, so the sample stays as drawn.
