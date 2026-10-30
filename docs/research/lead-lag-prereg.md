# When big stocks fall, do others rise? Plan written before looking at results

Written 2026-10-30, before running anything. Harsha asked whether there is a
relation where some stocks go up when big stocks go down, so that a fall in big
stocks tells you what to buy. To be usable, the relation has to show up the
**next** week. Stocks that rise in the same week as the fall are only known
afterwards. Two versions are tested, with data, universe, timing (signal at
the week's last close, trade at the next open), costs (0.30% a side in Mid) and
tax as in [weekly-rules-prereg.md](weekly-rules-prereg.md).

## L1: the market-level seesaw

- **Large** = traded-value ranks 1–100, **Mid** = 101–250, **Small** = 251–500
  (rebuilt each week, as before). Weekly returns of each group are equal weight.
- The question: does Large's return this week predict Mid's and Small's return
  next week? This is reported as the slope of next week's return on this week's
  Large return, with its t-stat, in discovery (2010–19) and holdout (2020–26).
  A negative slope is a seesaw (big down, others up next); a positive slope is
  a spillover (others follow big stocks down).
- **Trading version:** hold Mid equal weight for the week after any week in
  which Large fell more than 2%, and hold cash (6% a year) otherwise. It passes
  if its average return in those weeks is above Mid's average in all weeks,
  with t > 2 in discovery and above it again in holdout.

## L2: stock-to-stock lead-lag

- Each January, using only the previous 156 weeks: for every Mid stock, find
  the 10 Large stocks whose returns one week earlier have the strongest link
  (largest absolute correlation) with its returns, and fit the slope on each.
- Each week, the predicted next-week return of a Mid stock is the sum of those
  10 slopes times the 10 Large stocks' returns this week. Hold the 20 Mid stocks
  with the highest prediction, equal weight.
- Judged against Mid equal weight with the bar from the weekly study:
  discovery excess above 0 with t > 2, holdout excess above 0 and ahead in at
  least 4 of 7 years; to replace the monthly picks, it must also beat the
  monthly mid M6 after tax in the holdout.
- Thousands of stock pairs are scanned each year, so most links found will be
  chance. The walk-forward set-up (fit on the past, trade the next year) is
  what keeps that honest.
