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

The scikit-learn wrapper was not installed, so the model is trained through
LightGBM's own `lgb.train` with the same settings (300 rounds); nothing else changed.

## Results (run 2026-10-30)

`scripts/research/ml_model_study.py`, test years 2012 to Oct 2026, net of costs.

| | CAGR | After tax | Worst drawdown | Sharpe | 2020–26 after tax |
|---|---|---|---|---|---|
| **ML-M** (monthly, Mid) | +16.0% | +13.0% | -38.5% | 0.53 | +21.9% |
| Mid EW, monthly | +10.7% | +8.6% | -49.1% | 0.31 | +14.7% |
| **M6, the paper trial's rule** (same months) | **+19.6%** | **+15.9%** | -35.3% | **0.60** | **+25.4%** |
| **ML-W** (weekly, Liquid 250) | +15.2% | +12.2% | -44.1% | 0.48 | +13.4% |
| Liquid 250 EW, weekly | +13.0% | +10.5% | -52.4% | 0.41 | +14.2% |

- **ML-M fails.** Over EW: +0.20%/mo in discovery (t 0.9), +0.57%/mo in the
  holdout (t 1.9), ahead 5 of 7 years. Against M6 it is behind in both
  periods (-0.31%/mo and -0.40%/mo).
- **ML-W fails.** Over EW: +0.08%/wk (t 1.1) in discovery, 0.00%/wk in the
  holdout.
- Most important features: the market's own 1- and 6-month return, 60-day
  volatility, 3- and 12-month returns. The model mostly rediscovers momentum,
  less cleanly than the one-line rule.

The paper trial's rule stays. A model trained on price and volume only adds
nothing over 6-month momentum here.
