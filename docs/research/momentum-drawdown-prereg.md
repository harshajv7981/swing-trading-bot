# Mid-cap momentum drawdown control: plan written before looking at results

Written 2026-10-29, before running anything. Harsha said yes to testing two
changes that the published evidence suggests could cut mid-cap M6's drawdowns
(about -62% in [momentum-broad-prereg.md](momentum-broad-prereg.md)):

- a market trend filter;
- slower rebalancing.

The main source is a survivorship-free NSE backtest (top 200 by turnover,
2004–24, after costs and tax; github.com/bHandreaPeR/nse-momentum-research).
In it:
- holding cash when an equal-weight market index was below its 8-month average
  cut the worst drawdown from -74% to -27%;
- quarterly rebalancing beat monthly on Sharpe.

That sample includes 2008, which ours does not, so the effect has to be
re-tested here.

## Data, universe and costs (unchanged from the mid-cap study)

- eod2_data daily bars, EQ series, jump-adjusted (`swingbot.momentum.load`),
  prices without dividends.
- **Universe:** the mid bucket is rebuilt at each month-end. It is ranks 101–250
  by 60-session median traded value, among stocks with at least 260 sessions of
  history and a median value of at least ₹5 crore.
- **Cost:** 0.40% on each unit of one-way turnover, as in the mid-cap study.
- **Weights:** they drift with prices between rebalances in every arm (the
  earlier study reset them monthly). So arm A's numbers can differ slightly
  from the -62% / t 2.1 reported before.

## Arms

- **A, baseline:** the 20 mid-bucket stocks with the best return from 6 months
  back to 1 month back. Equal weight, rebalanced every month-end. This is the
  paper trial's rule.
- **B, trend filter:** A, plus a check at each month-end on the equal-weight
  mid-bucket index (the EW portfolio's value, cumulated):
  - If it is below the average of its last 8 month-end values (that month
    included), the whole book moves to a liquid fund for the next month, at 6%
    a year (0.487% a month).
  - Otherwise the book holds A's picks.
  - Selling into cash and buying back pay the usual cost on the turnover.
  - The 8 months come from the published study and are fixed here. 6 and 10
    months are printed as a sensitivity check only, and are not eligible to pass.
- **C, quarterly:** A's rule, run only at the end of Mar, Jun, Sep and Dec. The
  same 20 names are held, drifting, in the months between.
- **B+C together:** printed for information only, and not eligible. With two
  arms judged, one could pass by luck. A combined arm would need its own
  forward evidence.

## Split and pass bar

The split is the same as before:
- **Discovery:** Jan 2010 – Dec 2019.
- **Holdout:** Jan 2020 – Sep 2026.

An arm (B or C) passes only if all three hold:

1. **Drawdown:** its worst drawdown over Jan 2010 – Sep 2026, measured on month-end
   values, is shallower than A's by at least a third. For example, A at -62%
   needs -41.3% or better.
2. **Return kept:** its mean monthly excess over the mid-bucket EW is no more
   than 0.20 percentage points below A's in **both** discovery and holdout.
3. **After tax, beats the index fund:** after 20% short-term gains tax, its CAGR
   beats the Nifty Midcap150 Momentum 50 price index after an annual fee and
   long-term gains tax at the end.
   - **Short-term tax:** paid each calendar year on that year's net gain. Losses
     carry forward against later gains.
   - **Index fund:** 0.4% a year fee, then 12.5% on the total gain at the end.
     The ₹1.25 lakh exemption is ignored.
   - **Period:** the months the index file covers inside Jan 2010 – Sep 2026.
     Both are price returns, without dividends.
   - **Arm A gets the same check.** It is reported because it answers whether
     running our own picks beats just buying the fund.

## What is reported

For A, B, C and B+C:
- **Returns and risk:** CAGR, worst drawdown, and mean excess and t-stat in each
  period.
- **Trading:** months in cash (B), turnover a month, and after-tax CAGR.
- **Per year:** returns for each calendar year and for the index.

## What follows from the result

- **The paper trial does not change either way.** Its judging rules
  ([momentum-paper-trial.md](momentum-paper-trial.md)) forbid hand fixes mid-trial.
- **If B or C passes:** it runs next to the trial as a shadow ledger from the
  next month-end, and the Oct 2027 review compares the two.
- **If neither passes:** the drawdown plan stays a sizing decision (how much of
  the capital goes to this strategy), not a rule change.
- **If A fails check 3:** the October 2027 review should also weigh simply
  holding the index fund.
