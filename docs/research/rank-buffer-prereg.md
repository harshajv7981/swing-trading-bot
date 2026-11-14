# Rank-buffer M6: plan written before looking at results

Written 2026-11-14, before running anything. Build 4 of
[roadmap-2026-11.md](roadmap-2026-11.md).

M6 turns over about half its portfolio every month. Each turn costs 0.40%,
and every gain becomes short-term. A rank buffer is the best-documented
cheap fix ([Novy-Marx & Velikov 2016](https://www.nber.org/papers/w20721)):
keep a holding until it falls well down the ranking, not the moment it drops
out of the top 20. The drawdown study's quarterly arm, which also traded
less, raised the after-tax return (+13.7% against +12.3%) but failed on
drawdown ([momentum-drawdown-prereg.md](momentum-drawdown-prereg.md)).

**Trial count:** this adds **1 momentum trial (N 87 → 88)**. The buffer
width of 40 is fixed here, and no other width will be tried.

**Context:** [passive-twin-prereg.md](passive-twin-prereg.md) already sent any
real money to the Momentum 50 fund. A pass here would not reverse that.
Revisiting the fund question for a buffered M6 would need its own
pre-registration and ledger row. This test only says whether the buffer is
the better way to run M6.

## Data and rule

Everything is as `m6_and_ew` in `momentum_gold_study.py`: eod2_data, the mid
bucket (ranks 101–250 by 60-day median traded value, at least ₹5 crore),
6-1 month momentum, monthly, equal weight, and 0.40% per unit of one-way
turnover. Months: Jan 2010 – Sep 2026.

- **A, M6 (baseline):** the top 20 by momentum each month.
- **R40 (judged):** each month-end, rank the bucket by momentum.
  - A current holding is kept if it is still in the bucket and ranks 40th or
    better.
  - The free slots go to the best-ranked names not already held, until there
    are 20.
  - All 20 are reset to equal weight, the same as M6.

## Tax

20.8% on each calendar year's net gain, with losses carried forward
(`after_tax_cagr`). This model treats every year's gain as short-term
whatever the turnover, so it credits the buffer only with lower costs and
not with any tax deferral. That is harsher on R40.

## Pass bar (judged)

R40 passes only if both hold:

1. **After-tax CAGR at least 0.5 points a year above A's in both** discovery
   (2010–19) and holdout (2020–26).
2. **Its discovery excess over the mid EW has a t-stat of at least 2.**

**If it passes:** R40 replaces M6 as the version any future real-money case
would be built on. That would need its own fund comparison first. The
running paper trial stays on M6 as booked.

**If it fails:** M6 stays as it is, and no other buffer width is tried.

**Reported for information, not judged:**
- monthly turnover;
- worst drawdown;
- holdout excess over the mid EW;
- R40 against the Momentum 50 fund after tax, over the full and live windows,
  using the method in `passive_twin_study.py`.

## Results (run 2026-11-14)

`scripts/research/rank_buffer_study.py`. Its self-check passed: with the
buffer set to 20, the rule reproduces M6's monthly returns exactly.

| Arm | Period | CAGR | After tax | Excess over EW (t) | Worst drawdown | Turnover/month |
|---|---|---|---|---|---|---|
| A, M6 | full | +15.4% | +12.5% | +0.60% (+2.9) | -59% | 50% |
| A, M6 | 2010–19 | +4.8% | +3.8% | +0.38% (+1.8) | -59% | 50% |
| A, M6 | 2020–26 | +33.2% | +26.6% | +0.91% (+2.2) | -33% | 49% |
| R40 | full | +16.3% | +13.2% | +0.66% (+3.0) | -58% | 38% |
| R40 | 2010–19 | +4.2% | +3.2% | +0.33% (+1.6) | -58% | 39% |
| R40 | 2020–26 | +36.8% | +29.5% | +1.15% (+2.5) | -30% | 36% |

**For information:** R40 against the Momentum 50 fund after tax was +13.2%
against +20.5% over the full window, and +23.8% against +14.5% over the live
window.

**Verdict: FAIL on both checks.**
- **After tax against M6:** -0.6 points in 2010–19 and +2.9 in 2020–26. It
  needed +0.5 in both.
- **Discovery t:** 1.6, against a bar of 2.

M6 stays as it is, and no other width will be tried. The buffer cut turnover
by only about a quarter, from 50% to 38%. Its whole gain came in the
2020–26 mid-cap boom, which is the same pattern as M6 itself.
