# A machine-learning stock ranker: plan written before looking at results

Written 2026-10-30, before running anything. Harsha asked for a trained model.
This tests whether a gradient-boosted tree model (LightGBM), fed only price
and volume features, picks better stocks than the simple rules. Data, universes,
costs and tax as in [weekly-rules-prereg.md](weekly-rules-prereg.md).

## Two models

| Model | Universe | Rebalance | Holds | Trades at |
|---|---|---|---|---|
| **ML-W** | Liquid 250 | weekly | top 20 by predicted rank | next open, 0.25% a side |
| **ML-M** | Mid (101–250) | monthly | top 20 by predicted rank | month-end close, 0.20% a side (as the paper trial) |

## Features (all known at the signal close)

Returns over 1 week, 1, 3, 6 and 12 months, and 6-1 months; distance from the
252-day high and from the 200-day average; 20- and 60-day volatility; 5/50-day
volume ratio; log of 60-day median traded value; 14-day RSI; the universe's
equal-weight 1-month and 6-month return (market state). Each stock feature is
also given as its percentile within that date's universe.

## Target and training

- Target: the percentile rank of the stock's next-period return within its
  universe (so the model learns to sort stocks, not to call the market).
- Walk-forward: retrained every January on all earlier periods, with the last
  period before the cut dropped so no training label overlaps the test year.
  First test year 2012 (two-plus years of training).
- Fixed settings, chosen before any run and never tuned: 300 trees, learning
  rate 0.03, 15 leaves, at least 300 rows per leaf, 80% row and feature
  sampling, seed 0.

## Bar

- **Discovery:** 2012–2019. **Holdout:** 2020–Oct 2026.
- Against equal weight of its own universe, as before: excess above 0 with
  t > 2 in discovery; above 0 and ahead in at least 4 of 7 holdout years.
- **ML-M must also beat the monthly mid M6** (the paper trial's rule, same
  months, same costs): mean monthly excess over M6 above 0 in both periods and
  a higher after-tax CAGR in the holdout. Otherwise the paper trial's rule stays.
- Feature importances are reported so the model is not a black box.
