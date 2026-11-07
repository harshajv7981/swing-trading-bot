# US history study (phase 1): rules fixed before the data is pulled

Written 2026-11-07, before any US history beyond the 12-month replay was
fetched. Phase 1 of
[parallel-markets-learning-plan.md](../parallel-markets-learning-plan.md):
a research-only US worker. Nothing here places an order, and nothing here
changes the US paper trial
([us-momentum-prereg.md](us-momentum-prereg.md)), which stays as registered.

## Questions

1. Did the trial's rule (12-1 momentum, top 20) beat simply holding every
   S&P 500 member in equal weight, after costs, over the years Alpaca has data
   for, using the members the index really had at each month-end?
2. Does the NSE rule's 6-1 look-back, ported unchanged, do as well in the US?
3. Does a simple trend filter cut the worst falls, and at what cost in return?

## Data (frozen, regenerable)

- **Prices:** Alpaca daily bars, split- and dividend-adjusted (`adjustment=all`),
  SIP feed, from 2016-01-01 (Alpaca's history starts in 2016) to the last
  month-end before the run. Month-end closes are the last session of each month.
- **Members:** today's S&P 500 list from Wikipedia, walked back through its
  table of changes, giving the members at each month-end. Prices are fetched
  for every symbol that was a member at any time in the window.
- **Frozen panel:** the daily closes are written once as `closes.csv.gz` with
  a manifest (source, feed, adjustment, dates, symbol count, symbols with no
  data, SHA-256 of the file). The study reads only the panel. Re-running the
  study on the same panel must give identical numbers. Re-fetching later can
  differ, because adjusted prices are rewritten after each new dividend or
  split. The manifest records when the panel was fetched.
- **The panel is not published.** It is Alpaca's data, and the repo is public.
  It lives in the Actions cache and on the machine that built it. Only the
  report goes on the `us-trial-state` branch.

## Known biases, stated now

- **Survivorship:** a former member with no Alpaca bars (delisted before or
  soon after 2016, or acquired) cannot be held. A holding with no next
  month-end close is counted flat for that month (sold at its last month-end
  close), which hides takeover premiums and bankruptcies alike. The report
  lists the symbols with no data and the flat months.
- **Ticker reuse:** Wikipedia's change table names tickers, and a ticker can
  later belong to another company. Not corrected.
- **Cash earns 0%:** the trend rules sit in cash, so their return is understated
  by the T-bill rate (about 2 to 5% a year over the window).
- **One path:** about ten years and one broad bull market. A t-statistic is
  reported, not a verdict.

## Strategies (fixed, nothing tuned)

All book on month-end closes, hold to the next month-end, and pay 0.05% a side
on the value traded (the trial's slippage; Alpaca charges no commission). They
are rebuilt with the trial's band (a holding still picked is traded only when
more than 25% of a slot away from its slot). The same costs at 0.15% a side are
reported as a stress test.

| Name | Rule |
|---|---|
| **M12** | The trial rule: top 20 members by 12-1 momentum, equal weight. |
| **M6** | Top 20 members by 6-1 momentum (the NSE rule's look-back). |
| **TREND** | SPY when SPY's month-end close is above its 10-month average, else cash. |
| **M12+TREND** | M12 when SPY is above its 10-month average, else cash. |

## Benchmarks

- **EW:** every member at the month-end in equal weight, rebalanced monthly at
  no cost, from the same panel. Built from the same data, with the same
  survivorship gaps, so it is the fair test of the momentum signal. This is
  the main comparison.
- **SPY**, **RSP** and **SPMO** (SPMO from its first full month), as published
  funds.

## What is reported

Per calendar year and for the whole window: return, worst fall from a peak
(month-end), turnover, cost. For the whole window: compound annual return,
annual volatility, Sharpe ratio (cash at 0%), and the t-statistic of the
monthly return over EW.

## How the answers are read

1. **M12 vs EW** at 0.05% a side over the window. A higher compound return,
   with a t-statistic of 2 or more on the monthly difference, counts as
   support. A higher return with a lower t-statistic counts as "consistent,
   not shown". A lower return means the trial's rule did not add value in the
   US over these years. The trial continues either way: it is pre-registered,
   and paper costs nothing. The October 2027 review reads this result beside
   the trial's own.
2. **M6 vs M12:** reported side by side. A better M6 changes nothing now. A
   switch would need its own pre-registration and its own paper trial.
3. **Trend rules:** they help if their worst fall is at least a third smaller
   than their unfiltered version's, and they give up less than 2 percentage
   points of compound annual return. If so, a trend-filtered variant becomes a
   candidate for a later, separately registered trial, not a change to this one.

No parameter (look-backs, 20 names, 10 months, the band) is varied in this
study. Varying them is an overfit check for later, in the style of
[momentum-overfit-prereg.md](momentum-overfit-prereg.md).
