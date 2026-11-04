# TradingAgents paper test: rules fixed before the first run

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
- **Model:** `claude-haiku-5-5` for both the quick and the deep tier. One round
  of bull/bear debate and one round of risk debate (the framework's defaults).
- **Analysts:** market, news, fundamentals. The social analyst is left out: it
  reads StockTwits and Reddit, which barely cover NSE stocks. Global news
  queries are switched from US topics to India ones (Nifty, RBI, FPI flows,
  rupee and oil).
- **Stocks:** 20 from the Nifty Midcap 150 list in `data/universe/midcap150.csv`,
  drawn with `random.Random(20261102).sample(sorted(symbols), 20)`:
  ASTERDM, AUBANK, BAJAJHFL, CRISIL, FEDERALBNK, GODFRYPHLP, GODREJPROP, GVT&D,
  LICHSGFIN, MFSL, NH, NHPC, NMDC, PATANJALI, PIRAMALFIN, SRF, SWIGGY,
  UNOMINDA, WAAREEENER, YESBANK. A test checks the draw.
- **When:** one decision per stock for every Friday from 6 Nov 2026, run on the
  Mac on Friday evening or the weekend (`swingbot-agents run`). Data comes from
  Yahoo, as TradingAgents ships it; the cloud machine can't reach Yahoo.
- **Memory:** TradingAgents keeps a log of its past calls and their outcomes and
  reads it on later runs. The trial gets its own log
  (`data/state/agents_trial/trading_memory.md`), so it learns only from its own calls.

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

## Cost limit

Estimated before the first run at about $0.05 per stock, roughly $4–5 a month.
`swingbot-agents run` logs the tokens and dollars of every call. The first run
is a single stock (`--limit 1`) to measure it. If the measured monthly cost is
above $10, the test pauses until Harsha agrees to the new figure.
