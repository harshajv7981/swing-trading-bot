# Is mid-cap M6 overfit? Plan written before looking at results

Written 2026-11-05, before running anything. Mid-cap M6 passed
[momentum-broad-prereg.md](momentum-broad-prereg.md) with t 2.1 in both
periods. Its Sharpe was 0.18 in 2010–19 and 0.88 in 2020–26. This asks whether
M6 sits on a broad plateau of similar rules, or is a lucky peak picked from
many tries. Method from the walk-forward-validation skill (Bailey and López de
Prado's deflated Sharpe ratio and probability of backtest overfitting).

**Nothing here changes the paper trial.** Its rules are fixed in
[momentum-paper-trial.md](momentum-paper-trial.md). Whatever this finds, the
trial keeps running M6 as booked. If a neighbour does better, that is not a
reason to switch, because picking the best of 72 is exactly the overfitting
being tested. The result only sets how much to expect from the trial, and feeds
the October 2027 review.

## Data and baseline (same as the broad study)

- eod2_data daily bars, EQ series, `adjust_jumps` applied, via `swingbot.momentum.load`.
- Universe at each month-end: 260+ sessions, 60-session median traded value of at
  least ₹5 crore, ranked by that value.
- Months held Jan 2010 to Sep 2026. Costs 0.40% a round trip on the fraction replaced.
- **Excess** is a rule's monthly return minus the equal-weight return of the same
  bucket, after both pay costs. Everything below is judged on excess.

## The grid: 72 neighbours of M6

| Setting | Values | M6 |
|---|---|---|
| Lookback (months) | 3, 6, 9, 12 | 6 |
| Skip the latest month | 0, 1 | 1 |
| Stocks held | 10, 20, 30 | 20 |
| Bucket (traded-value ranks) | 76–225, 101–250, 126–275 | 101–250 |

M6 is one cell. Fewer than 30 stocks were in the bucket in 2010–13, so the
30-stock rules held the whole bucket then (excess about zero). That is left as it is.

## Tests and pass rules

1. **Plateau.** Full-period mean monthly excess for all 72 rules.
   Pass if at least 75% of them are above zero **and** the median rule's excess
   is at least half of M6's.
2. **Probability of backtest overfitting (PBO), by CSCV.** The 200 months are cut
   into 16 blocks of equal length (the last takes the remainder). For each of the
   12,870 ways to pick 8 blocks as in-sample, take the rule with the best
   in-sample Sharpe of excess and find where it ranks out of sample. PBO is the
   share of splits where it lands below the out-of-sample median.
   Pass if PBO is below 0.50.
3. **Deflated Sharpe ratio (DSR)** for M6's monthly excess. The number of trials
   counts the 72 grid rules plus 15 earlier momentum variants in this repo
   (Nifty 100 ×3, broad ×6, smooth, low-vol, drawdown and gold studies ×6), so
   N = 87. The spread of Sharpe across trials comes from the 72 grid rules. The
   test uses per-month Sharpe, with skew and kurtosis from M6's excess series.
   Pass if DSR is at least 0.95.
4. **Walk-forward choice (reported, not a pass rule).** Each January from 2015,
   pick the grid rule with the best mean excess over the previous 60 months, and
   hold it for that year. Chain the years to Sep 2026 and compare the result with
   M6 held throughout. This shows whether a trader re-choosing settings each year
   would have done as well, and how often the choice lands near M6.

## Verdict

- **Robust:** tests 1, 2 and 3 all pass.
- **Real but overstated:** tests 1 and 2 pass and 3 fails. The edge is broad, but
  its size is not secure. Read the trial's results with that in mind.
- **Fragile:** test 1 or test 2 fails. M6 looks like a lucky cell. Write that into
  the trial doc, so the October 2027 review starts from a lower prior.

Code: `scripts/research/momentum_overfit_study.py`. Results are added below once
it has run, and none of the rules above will change after that.

## Results (run 2026-11-05)

`scripts/research/momentum_overfit_study.py`, Jan 2010 to Sep 2026 (201 months).
The M6 cell matches the broad study: +0.41%/month (t 2.1) in 2010–19 and
+0.88%/month (t 2.1) in 2020–26.

| Test | Result | Bar | |
|---|---|---|---|
| 1. Plateau | 72 of 72 rules beat EW; median +0.55%/month vs M6 +0.60% | 75%, and half of M6 | pass |
| 2. PBO | 0.42; the in-sample best's median out-of-sample rank is 0.59 | below 0.50 | pass |
| 3. DSR | 0.78: M6's Sharpe of excess is 0.207 a month (0.72 a year), against 0.151 expected from the best of 87 by luck | 0.95 | fail |
| 4. Walk-forward | re-choosing each January: +1.16%/month (t 2.8), against M6 held throughout: +0.83% (t 2.9), 2015–Sep 2026 | reported only | |

**Verdict: real but overstated.**

- **The edge is broad.** Every one of the 72 neighbours beat its bucket's equal
  weight. M6 sits in the middle (rank 32 of 72), not on a peak. The settings
  barely matter, with two exceptions:
  - A 3-month lookback is weaker (median +0.28%/month).
  - Holding 10 stocks instead of 20 earns more on average (+0.99%/month), which
    is the usual trade of more return for more single-stock risk.
- **Picking the best setting does not help much.** A PBO of 0.42 means the
  in-sample winner lands below the out-of-sample median 42% of the time, close
  to a coin flip. That fits a plateau: the cells are close, so ranking them is
  mostly noise.
- **The size of the edge is not secure.** After allowing for 87 tries, the
  chance that M6's excess Sharpe is above zero is 0.78, short of 0.95. Treat the
  backtest's +0.6%/month over EW as an upper estimate. A real figure nearer
  +0.3%/month would be no surprise.
- **The walk-forward chain earned more, but that is no case for switching.**
  It almost always chose 10-stock rules with long lookbacks. That is the same
  idea in a more concentrated form, and t 2.8 is no stronger than M6's 2.9. As
  pre-registered, the trial keeps M6.

**For the October 2027 review:** a year of the paper account trailing mid-cap EW
is quite possible even if the edge is real. Its excess Sharpe of about 0.7 a
year means a losing year about one time in four. Survivorship bias for fully
delisted stocks, noted in the broad study, would also shrink the edge, and none
of these tests can see it.
