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

## Data fix found on the first run (before reading any verdict)

The first run turned up two problems in the files. Both are fixed for every
portfolio alike:
- **Non-stocks in the folder.** It holds 167 index series and a few hundred ETFs
  and debt funds. They are now skipped by name (spaces, "etf", "bees", "nifty",
  "sensex", "liquid", "gilt", "gsec"), which leaves 2,283 stocks with enough history.
- **Some corporate actions are unadjusted.** Examples: Bharti's 2009 bonus; the
  Adani Enterprises, Siemens, Tata Motors and Vedanta demergers; and the 10:1
  NIFTYBEES split of 19 Dec 2019. NSE price bands cap most stocks at 20% a day,
  so a one-day fall of more than 40% is now treated as a corporate action, and
  earlier prices are scaled by it (`adjust_jumps`). Rises are left alone: they
  are mostly gaps where a stock traded outside the EQ series.

The Nifty 100 test was rerun with the same fix. Its verdicts are unchanged:
- **M6:** passes, at +0.76%/mo (t 2.7) in discovery and +0.53%/mo (t 1.0) in the holdout.
- **M12:** fails, at t 1.8 in discovery.
- **M12F:** fails.

The unadjusted NIFTYBEES split also touches the earlier threshold study
(`threshold_study.py`, whose `mkt_above200` reads NIFTYBEES). From Dec 2019 to
about Sep 2020 that study wrongly saw the market as below its 200-day average,
so its 2020 "defensive" figure needs a rerun.

## Results (run 2026-10-29)

`scripts/research/momentum_broad_study.py`, Jan 2010 to Sep 2026. Excess is per
month over the same bucket's equal-weight portfolio, after costs.

| Bucket | Portfolio | CAGR | Worst drawdown | Discovery 2010–19 (t) | Holdout 2020–26 (t) | Holdout years beating EW | Verdict |
|---|---|---|---|---|---|---|---|
| Large (top 100) | EW | +7.6% | -46% | | | | |
| | M6 | +8.9% | -46% | +0.12% (0.5) | +0.24% (0.5) | 4/7 | fail |
| | M12 | +12.6% | -38% | +0.46% (1.5) | +0.33% (0.8) | 4/7 | fail |
| Mid (101–250) | EW | +7.8% | -63% | | | | |
| | **M6** | +15.1% | -62% | +0.41% (2.1) | +0.88% (2.1) | 6/7 | **pass** |
| | M12 | +16.9% | -60% | +0.32% (1.4) | +1.36% (3.3) | 7/7 | fail (discovery t) |
| Small (251–500) | EW | +5.7% | -62% | | | | |
| | M6 | +5.1% | -62% | -0.08% (-0.9) | +0.04% (0.1) | 4/7 | fail |
| | M12 | +7.5% | -64% | -0.10% (-1.3) | +0.56% (1.5) | 4/7 | fail |

How big each bucket was:
- **Large:** 100 every year.
- **Mid:** about 20 stocks a year in 2010–13, 65–100 in 2014–16, and 140–150 from 2017.
- **Small:** none until 2017, and 210–250 only from 2021.

At ₹5 crore a day, few stocks qualified in the early years. So the mid
discovery result rests mostly on 2014–19, and the small bucket has almost no
discovery data at all.

**What this says:**
- **Mid caps are where momentum shows up.**
  - **M6 passes in both periods** (t 2.1 and 2.1) and beat the mid-cap average in
    6 of 7 recent years.
  - **M12 is even stronger since 2020** (t 3.3, 7 of 7 years), but it failed the
    discovery bar, so it does not count.
  - With 6 portfolios tried, one pass could be luck. Two closely related versions
    both doing well since 2020 makes that less likely.
- **Large caps show no momentum edge** once the universe is chosen without hindsight.
  - Both versions fail.
  - Yesterday's Nifty 100 result (M6 at +27% CAGR) was mostly survivorship: today's
    list already holds the winners.
- **Small caps show nothing reliable.** There is no early data, and since 2020
    M6 is flat and M12 is not significant.
- **The risk is large.** Mid-cap portfolios, momentum or not, fell about 60% at
  their worst. A ₹10 lakh account would have been worth about ₹4 lakh at the bottom.
  Survivorship bias also remains for fully delisted stocks, which matters more in
  mid and small caps.

**Next step:** a forward paper trial of mid-cap M6, with the universe rebuilt
monthly by the rule above. It needs no model calls. Real money is out of the
question until it has at least 12 months of paper results and a plan for the
drawdowns.
