# Momentum beyond the Nifty 100: plan written before looking at results

Written 2026-10-29, before running anything. Harsha asked whether mid caps and
small caps behave differently. This repeats the momentum test of
[momentum-prereg.md](momentum-prereg.md) on every NSE stock eod2_data has, and
splits it by size.

## Data and universe

- eod2_data daily bars for all 3,761 files (EQ series only, split-adjusted,
  prices without dividends).
- The universe is rebuilt at each month-end from information known that day, not
  from today's index lists, which removes most of the hindsight in the Nifty 100
  test. A stock qualifies if it has:
  - at least 260 sessions of history;
  - a median daily traded value (close times volume) over the last 60 sessions of
    at least ₹5 crore.
- Qualifying stocks are ranked by that traded value. The buckets mirror SEBI's
  100 / 150 / 250 split by size; there is no share-count data, so traded value
  stands in for market value.
  - **Large:** ranks 1–100.
  - **Mid:** ranks 101–250.
  - **Small:** ranks 251–500.
- **Bias that remains:** stocks fully delisted are missing (DHFL, Jet Airways and
  Satyam are not in the files), though collapsed ones still listed are present
  (RCOM, Unitech, Yes Bank). Results still lean optimistic, less than before.

## Portfolios (per bucket, month-end rebalance, held one month)

- **EW:** equal weight in every stock in the bucket (the baseline).
- **M6:** the 20 stocks in the bucket with the best return from 6 months back to
  1 month back, equal weight.
- **M12:** the same, from 12 months back to 1 month back.
- **Costs** per round trip on the fraction replaced, higher for thinner stocks:
  0.25% large, 0.40% mid, 0.60% small.

## Split and pass bar (same as before)

- **Discovery:** Jan 2010 – Dec 2019. **Holdout:** Jan 2020 – Sep 2026.
- **Discovery pass:** mean monthly excess over the bucket's EW above 0, with a
  t-stat above 2.
- **Holdout pass:** mean monthly excess above 0, and beating EW in at least 4 of
  the 7 holdout years.
- It must pass both. With 6 portfolios tried (2 per bucket), one may pass by luck.
- Also reported: CAGR, the worst drawdown, turnover and the bucket's size each year.
