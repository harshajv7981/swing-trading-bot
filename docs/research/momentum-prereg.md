# Multi-month momentum: plan written before looking at results

Written 2026-10-29, before running anything. The swing rules and the news calls
showed no edge ([price-rules-prereg.md](price-rules-prereg.md)). Momentum is the
most studied anomaly in equities: stocks that rose most over the past 6–12
months keep beating the rest for a few months (Jegadeesh & Titman 1993). The
effect has been reported in Indian stocks too. This tests whether it holds on our
universe after costs. It is a monthly portfolio strategy, not a swing trade, so
it would be a different product from the current bot.

## Data

- The same eod2_data daily closes as the price-rule study (104 current Nifty 100
  stocks, split-adjusted), plus NIFTYBEES.
- Prices only, so no dividends. That understates every portfolio about equally.
- **Survivorship bias:** today's index members look better in hindsight. This
  flatters every portfolio built from them, so each one is judged against an
  equal-weight portfolio of the same stocks, not against zero or the index.

## Portfolios

All rebalance on the last trading day of each month and hold until the next one.
- **Eligible:** a stock with at least 260 sessions of history on the rebalance day.
- **Costs:** 0.25% on the value traded each rebalance (round trip 0.25% per
  name swapped).

The portfolios:
- **EW (baseline):** equal weight in every eligible stock.
- **M12:** the 10 eligible stocks with the best return from 12 months back to 1
  month back (skipping the latest month), equal weight.
- **M6:** the same, but over 6 months back to 1 month back.
- **M12F:** M12, but all cash (0% return) for any month that starts with
  NIFTYBEES below its 200-day average.

## Split and pass bar

- **Discovery:** monthly returns from Jan 2010 to Dec 2019. **Holdout:** Jan 2020
  to the last full month (Sep 2026). Nothing is changed after seeing discovery.
- Measured on the monthly excess return over EW, after costs.
- **Discovery pass:** mean monthly excess above 0 with a t-stat above 2.
- **Holdout pass (the one that counts):** mean monthly excess above 0, and the
  portfolio beats EW in at least 4 of the 7 holdout calendar years (2020–2026,
  2026 being partial).
- A portfolio must pass both periods. With 3 tried, one may pass by luck.
- Also reported, not part of the bar: CAGR, the worst drawdown, and turnover.
