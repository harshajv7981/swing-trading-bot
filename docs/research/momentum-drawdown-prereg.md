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

## Results (run 2026-10-29 on the Mac, eod2_data to 23 Oct 2026)

`scripts/research/momentum_drawdown_study.py`, Jan 2010 – Sep 2026, 2,283 stocks.
Excess is per month over the mid-bucket EW, after costs. The mid-bucket EW
itself made +7.8% CAGR, with a worst drawdown of -62.6%.

| Arm | CAGR | Worst drawdown | Discovery excess (t) | Holdout excess (t) | Turnover/month | Months in cash | After 20% STCG | Verdict |
|---|---|---|---|---|---|---|---|---|
| A, monthly M6 | +14.9% | -61.7% | +0.40% (2.0) | +0.87% (2.1) | 51% | 0 | +12.3% | baseline |
| **B, 8-month filter** | +13.0% | -41.2% | +0.10% (0.2) | +0.53% (0.8) | 48% | 72 | +10.7% | **fail** (drawdown misses the -41.2% bar by under 0.05 pt; discovery excess falls 0.30 pt) |
| **C, quarterly** | +16.6% | -52.1% | +0.69% (2.7) | +0.70% (1.8) | 25% | 0 | +13.7% | **fail** (drawdown short of -41.2%; return and fund checks pass) |
| B+C (info only) | +12.2% | -38.7% | +0.21% (0.4) | +0.17% (0.3) | 35% | 72 | +9.9% | not eligible |
| B6 (sensitivity) | +16.1% | -24.6% | +0.31% (0.5) | +0.79% (1.2) | 46% | 74 | +13.2% | not eligible |
| B10 (sensitivity) | +14.0% | -31.9% | +0.31% (0.5) | +0.40% (0.6) | 46% | 73 | +11.5% | not eligible |

**Index fund check.** The index file only starts in Aug 2022, so this check
covers just 49 months (Sep 2022 – Sep 2026). Over that window:
- the Momentum 50 index made +16.0% a year at price, with a worst drawdown of -24.2%;
- held as a fund, after the fee and long-term tax, that is +14.0%;
- every arm beat it after short-term tax (A +24.6%).

Four years is too short to settle the question.

**What this says:**
- **Neither change passes, so the paper trial stays as it is.**
- **The trend filter cut drawdowns but cost too much return.** In the 2010s it
  sat in cash through rebounds, and its discovery-period excess fell from
  +0.40% to +0.10% a month.
- **The filter's effect depends on the window length.** 6, 8 and 10 months give
  worst drawdowns of -25%, -41% and -32%, with no pattern by length. That is
  the sign of a result driven by a few dates, mainly 2011, not a stable rule.
  The 6-month figure looks good only after the fact, and choosing it now would
  be curve fitting.
- **Quarterly rebalancing is the more promising change, but it did not pass.**
  - It had higher returns than A in discovery (t 2.7), half the turnover, and
    a shallower drawdown (-52% vs -62%).
  - It missed only the drawdown bar, which was set at a third shallower.
  - Since it passed the other two checks, it is the candidate for a second
    forward test if Harsha wants one, judged as a new pre-registered trial
    and not as a pass here.
- **A ~60% drawdown stays part of this strategy.** Managing it is a sizing
  decision: put in only money you could watch fall by more than half without
  selling.
