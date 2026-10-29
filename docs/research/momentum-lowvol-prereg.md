# Mid-cap momentum plus low volatility: plan written before looking at results

Written 2026-10-29, before running anything. Harsha said yes to testing whether
adding low volatility to mid-cap M6 cuts its drawdowns (about -62%) while still
beating the mid-cap average.

Two earlier results led here:
- **Neither drawdown fix passed.** The trend filter and quarterly rebalancing
  both failed their bar ([momentum-drawdown-prereg.md](momentum-drawdown-prereg.md)).
- **Low volatility has the smallest drawdowns of the Indian factors.** In a
  Nifty 500 study (Dec 2006 – Dec 2025, after costs and tax), 25-stock
  low-volatility portfolios:
  - made about 14% a year, against 10.4% for the Nifty 50;
  - had a worst drawdown of about -43%, the smallest of the factors tested;
  - kept that edge whether their stocks were heavily or lightly traded.

  The source is freefincal.com, "Does factor investing outperformance fail for
  high-turnover stocks?".

It needs only prices, which we already have.

## Data, universe and costs (unchanged from the drawdown study)

- **Prices:** eod2_data daily bars, EQ series, jump-adjusted
  (`swingbot.momentum.load`), without dividends.
- **Universe:** the mid bucket, rebuilt at each month-end. It is ranks 101–250
  by 60-session median traded value, among stocks with at least 260 sessions
  and a median value of at least ₹5 crore.
- **Rebalancing:** every month-end. Weights drift between rebalances.
- **Cost:** 0.40% on each unit of one-way turnover.

## Signals, measured at each month-end on data known that day

- **M6:** the return from 6 months back to 1 month back, as in the paper trial.
- **Volatility:** the standard deviation of daily returns over the last 252
  sessions. At least 200 valid days are needed, or the stock is skipped for the
  low-volatility rules. One year matches NSE's own low-volatility indices.

## Arms

- **A, baseline:** the top 20 by M6, equal weight. This is the paper trial's rule.
- **Blend (judged):** half the money in A's 20 stocks, equal weight, and half in
  the 20 lowest-volatility stocks in the bucket, equal weight. A stock in both
  lists gets both halves' weight.
- **Calm momentum (judged):** the top 20 by M6 among the calmer half of the
  bucket (volatility at or below the bucket median that month), equal weight.
- **L, low volatility alone:** the 20 lowest-volatility stocks. Information only,
  not eligible.

## Split and pass bar

The split is the same as before:
- **Discovery:** Jan 2010 – Dec 2019.
- **Holdout:** Jan 2020 – Sep 2026.

A judged arm passes only if both hold:

1. **Beats the mid-cap average.** This is the same bar as the momentum studies:
   - **Discovery:** mean monthly excess over the mid-bucket EW above 0, with a
     t-stat above 2.
   - **Holdout:** mean monthly excess above 0, and beating EW in at least 4 of
     the 7 holdout years.
2. **Drawdown.** Its worst drawdown over Jan 2010 – Sep 2026, on month-end
   values, is shallower than A's by at least a third.

With two arms judged, one could pass by luck, and the result will say so.

Also reported for every arm:
- **Returns and risk:** CAGR, Sharpe (monthly, against 6% cash) and turnover.
- **After tax:** CAGR after 20% short-term tax.
- **Index check:** over the months the Momentum 50 index file covers, which is
  only Sep 2022 onward, the arm against the index after fee and 12.5% long-term
  tax. This check is too short to judge on.
- **Per year:** returns for each calendar year.

## What follows from the result

- **The paper trial does not change either way.**
- **If an arm passes:** it runs as a shadow ledger next to the trial from the
  next month-end, and the October 2027 review compares the two.
- **If both pass:** the one with the shallower drawdown runs.
- **If neither passes:** low volatility does not fix the drawdown on our data,
  and the drawdown stays a sizing decision.
