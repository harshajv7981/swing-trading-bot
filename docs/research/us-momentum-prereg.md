# US large-cap momentum paper trial: rules fixed before it starts

Written 2026-11-07, before any US data was looked at and before the first
booking. This is step 1 of
[parallel-markets-learning-plan.md](../parallel-markets-learning-plan.md): the
US worker's first strategy, run forward on Alpaca's paper account. Nothing
here places a real order.

## Why this rule

Harsha asked for the strategy with the best evidence and set no minimum
result. The choice was made from published evidence, not from a backtest
here (none exists yet):

- **Cross-sectional stock momentum** (buy recent winners, hold a month) is the
  best-documented price anomaly. It survived out of sample after its 1993
  publication, though with long losing stretches and sharp crashes. Funds that
  run it on US large caps did better than the S&P 500 over the last 10 years
  (MTUM about 14% a year against about 12% for the index, to April 2026;
  SPMO by more).
- **ETF dual momentum** (switching between a few index ETFs) was rejected. An
  independent post-publication test (2014 to 2023) found 6.3% a year against
  10.8% for SPY, with the same worst fall.
- It is the same family as the NSE mid-cap rule (M6), the only rule that
  passed both periods in [momentum-broad-prereg.md](momentum-broad-prereg.md).
  Running it in both markets also tests whether that idea travels.

Published anomalies typically lose about half their edge after publication.
"Good profits" is therefore not promised: the trial checks whether the rule
beats simple index funds after costs.

## The rule (fixed, nothing to tune)

- **When:** the last NYSE session of each month (`config/us_holidays.toml`),
  ranked on that day's closes. The orders go to Alpaca paper as market orders
  at the next session's open. The first booking is the first month-end after
  the replay check below passes, and not before 30 Nov 2026.
- **Universe:** the S&P 500 members on the ranking day, from Wikipedia's
  constituents list, read at each booking and saved as a dated snapshot
  (`us-m12/universe/sp500-<date>.csv` on the state branch). A stock needs 13 month-ends of price history.
- **Signal:** the total return from 12 month-ends back to 1 month-end back
  (12-1 momentum, skipping the latest month), on split- and dividend-adjusted
  closes.
- **Picks:** the 20 highest, held in equal weight until the next month-end.
  Stocks that stay in the top 20 are not traded, except to fix an over- or
  under-weight of more than 25% of a slot. Sells go before buys.
- **Paper account:** $100,000 (Alpaca's paper default), whole shares, long
  only, no stops and no leverage. Costs are what Alpaca paper fills give, plus
  0.05% a side booked for slippage (Alpaca charges no commission). Cash left
  over from whole-share rounding stays in cash.
- **Code:** `swingbot-us-momentum` (`src/swingbot/us_momentum.py`), through
  `swingbot.markets` and the Alpaca adapter. It runs in GitHub Actions
  (`.github/workflows/us-momentum.yml`), and its state is kept on the
  `us-trial-state` branch. Added 2026-11-07, before the replay, the universe
  snapshot moved there from `data/universe/`; no rule changed.

## What it is compared with

All three are total return, from Alpaca daily bars:
- **RSP**: the equal-weight S&P 500, the rule's own universe without the
  signal. This is the main test.
- **SPY**: the S&P 500, the market as most people hold it.
- **SPMO**: the S&P 500 Momentum ETF. If the trial cannot beat it, buying
  SPMO is the simpler way to own the same idea.

## Replay check (before the start, not evidence)

Run the code over Nov 2025 to Oct 2026 on Alpaca historical data. This passes
if each month's picks and returns come out from the data with no hand fixes,
and the 12 months are reported against RSP, SPY and SPMO. These months were
seen before the rule was chosen (through fund returns), so the result checks
the code, not the edge.

## How it is judged

- **Every month:** the account value, the month's return and the return since
  the start against RSP, SPY and SPMO; turnover; costs; holdings; orders and
  fills; reconciliation with Alpaca (`AlpacaBroker.reconcile`, which must be
  empty); and the worst fall from a peak.
- **No early stop on returns.** It is paper money, and momentum has long
  losing stretches. The trial stops early only if the process breaks: a month
  that can't be booked, a reconciliation difference nobody can explain, or
  wrong prices.
- **At 12 bookings**, real money is discussed only if all of these hold:
  1. Every month was booked and reconciled with no hand fixes.
  2. The paper account beat RSP over the 12 months, after costs.
  3. Harsha has a written plan for a fall of 40 to 50%, the scale momentum
     funds have had in crashes.
  4. Tax and remittance for an Indian resident holding US shares (LRS
     limits, foreign-asset reporting, gains taxed in India) are checked, and
     so is whether Alpaca opens live accounts for Indian residents. Paper
     needs neither.

  Not beating SPMO does not fail the trial, but it means SPMO is the cheaper
  way to own the idea. Twelve months cannot prove an edge. The year checks that
  the rule and the plumbing behave, not that the edge is real.

## Sources

- [Swedroe: Is "Momentum" Faltering? Part II](https://www.etf.com/sections/index-investor-corner/swedroe-momentum-faltering-part-ii): long-run out-of-sample record, including decade-long losing periods.
- [Momentum factor investing: 30 years of out-of-sample data](https://alphaarchitect.com/momentum-factor-investing-30-years-of-out-of-sample-data/)
- [Price Action Lab: dual momentum after publication](https://www.priceactionlab.com/Blog/?p=68274): 2014 to 2023 against SPY.
- [PortfoliosLab: MTUM](https://portfolioslab.com/symbol/MTUM) and [MTUM vs SPMO](https://portfolioslab.com/tools/stock-comparison/MTUM/SPMO): 10-year fund returns. Snapshots, not checked against issuer data.
