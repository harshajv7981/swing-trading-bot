# FINSABER review (reviewed 2026-11-13)

Repo: https://github.com/waylonli/FINSABER  Paper: https://arxiv.org/abs/2505.07078
("Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?",
Li, Kim, Cucuringu, Ma; KDD 2026). Read from README and paper HTML only; no code run.
Paper appendices (rolling-window sizes, LLM cost table) were not fully read.

## What it is
A backtesting framework plus a paper. It re-tests LLM stock-timing agents (FinMem,
FinAgent) against simple baselines over 20 years and many symbols, and finds the
published LLM edge mostly disappears.

## Architecture
- Data loaders, execution model, metrics, result writers, stock selectors.
- Two engines: a Backtrader one for rule strategies, and a plain-Python one for LLM
  strategies that get date-level data and place orders.
- Execution defaults to next-day open, with slippage, commissions, liquidity caps.
- Data: a Hugging Face parquet set (S&P 500 prices, news, 10-K/10-Q), 2000-2025.
- LLM agents (FinMem, FinAgent, FinRL) sit in side folders, not in the package.

## Cost to run
- Framework is free and open source. Rule baselines need no LLM and no paid data.
- LLM agents were run on OpenAI GPT-4o / 4o-mini; paper calls this "financially
  intensive". Not runnable on a Claude plan without rewriting the agent calls.
- Data is a US set that the user downloads; licence and vendors are not stated.

## India fit
Poor out of the box: universe is S&P 500, news and filings are US. Feeding NSE daily
CSVs needs a custom `TradingData` loader (small), but you would lose the news/filings
side, and we already have our own backtests. No NSE index membership history is
included, so its survivorship handling does not transfer.

## Key findings and how measured
- Setup: 2004-2024, 63 to 91 distinct S&P 500 symbols per scenario (random five,
  momentum, low-vol, LLM-picked), reselected each rolling window, delisted names kept.
- LLM timers lose to buy-and-hold. Random Five Sharpe: B&H 0.32, FinMem -0.25,
  FinAgent 0.09. Momentum set: B&H 0.38, FinMem 0.03, FinAgent 0.10; ARIMA 0.54.
- Paired t-tests: B&H beats LLMs significantly (p about 3e-6 vs FinMem). Alpha not
  significant for either LLM (all p > 0.34); FinMem alpha negative everywhere.
- Regime split: LLMs too timid in bull runs, too aggressive in bear runs.
- The headline wins in the original papers came from 4 stocks over 6 months (TSLA etc).
  Rerun on 2004-2024, those four showed no robust edge; FinMem TSLA Sharpe 0.93
  vs 2.68 originally reported.
- Look-ahead: inputs aligned to pre-window info; news dated by day is usable only from
  the next decision. Authors admit pretrained LLMs may have seen test-period data, and
  argue it would only flatter the LLMs. No fix beyond that.
- Survivorship: handled by point-in-time S&P 500 membership including delisted stocks.

## Worth borrowing (ideas only, no code)
1. Always include buy-and-hold (here: Nifty 50) and a random-pick baseline next to any
   LLM call set. Our TradingAgents prereg already scores vs Nifty; add a "random sign
   on the same 10/20 stocks" null.
2. Many symbols across rolling windows beat a handful of stocks and one period; the
   cherry-pick of 4 names over 6 months is the exact trap. Keep our fixed watchlists.
3. Paired t-test of strategy vs buy-and-hold per window, and alpha significance, not
   just mean return. Paper: https://arxiv.org/html/2505.07078v5
4. Report by market regime (bull, sideways, bear) so a call set that only works in
   uptrends is visible. Cheap to add to `swingbot-agent-calls score`.
5. Count LLM cost inside performance. We already net 0.40% trading cost; note it is
   zero API cost here only because of the Claude plan.
6. Next-open execution default, with date-only news usable only from the next day.
   Check our scorecard uses the first close after the call (it does).

## Skip
- The framework itself, Backtrader engine, Hugging Face US dataset.
- FinMem / FinAgent / FinRL agents (OpenAI-paid, US-only, already shown weak).
- Their LLM look-ahead stance: they accept the leakage; our forward-only rule is
  stricter and correct.

## Verdict
Borrow ideas only (random-sign null, regime split, paired test vs index); do not adopt.
It backs our existing stance: LLM timing shows no edge over passive, so judge only
forward, as already preregistered.
