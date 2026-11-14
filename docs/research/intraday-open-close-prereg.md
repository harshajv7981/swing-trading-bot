# Open-to-close (intraday) rules on daily bars: plan written before looking at results

Written 2026-11-11, before running anything. Harsha asked whether intraday trading
with hard rules can be tested. The repo has only daily bars, so this tests the
part daily bars can show: buy at the open, sell at the same day's close.

## Data

eod2_data daily bars, EQ series, Jan 2010 to Sep 2026. A stock counts on a day
when its 60-session median traded value, known the day before, is at least
₹10 crore (R8's floor). Days whose open is more than 25% from the last close are
dropped as bad data or unadjusted splits.

## Rules (all long, bought at the open, sold at that day's close)

- **G-up:** the open is at least 0.5 ATR above the last close.
- **G-down:** the open is at least 0.5 ATR below the last close.
- **H20:** the last close was the highest close of 20 sessions.
- **Baseline:** every eligible stock, every day, traded the same way.

ATR is the 14-session Wilder ATR known at the last close.

## Hard limits

- A stop 1 ATR under the open on every trade. If the day's low reaches it, the
  trade exits at the stop. Daily bars don't show whether the high or the low
  came first, so the stop always counts.
- Long only, no borrowed money.
- At most 5 positions a day, each one fifth of the money. G-up and G-down take
  the 5 biggest gaps; H20 takes the 5 most traded. Empty slots stay in cash.
- Costs 0.10% a round trip.

## Split and pass bar (same as the other studies)

- Discovery 2010–19, holdout 2020–Sep 2026.
- **Discovery pass:** mean daily return per trade above the baseline's, t above 2.
- **Holdout pass:** the 5-slot account's mean daily return after costs above 0,
  and its trades beat the baseline's in at least 4 of 7 years.
- A rule must pass both. With 3 tried, one may pass by luck.

## Known limits

Nothing that depends on the first minutes of the day (opening-range breakout,
VWAP) can be tested here. A real open fill differs from the printed open.
Survivorship: eod2_data lacks fully delisted stocks.

## Results

Run 2026-11-11 by `scripts/research/intraday_open_close_study.py`: 1,264 stocks,
1,151,347 stock-days. The baseline lost 0.258% a trade.

| Rule | Trades | Discovery: per trade vs baseline (t) | Holdout: 5-slot account per day (t) | Holdout years beating baseline | Verdict |
|---|---|---|---|---|---|
| G-up | 12,951 | -0.776% (-16.9) | -0.928% (-25.6) | 0/7 | fail |
| G-down | 9,428 | +1.527% (+18.5) | +0.391% (+10.0) | 7/7 | pass by the bar, **not trusted** |
| H20 | 19,170 | -0.061% (-2.7) | -0.276% (-9.9) | 2/7 | fail |

**Why G-down's pass is not trusted** (checked after the run, so not evidence either way):

- In 30% of its trades the open was also the day's low. Those trades average
  +3.32%; the other 70% average -0.29%. The whole gain rests on buying at the
  first print of the day, which is the one price daily bars cannot show to be
  buyable. The rule also needs the open to rank the gaps, so a real order goes
  in after that print.
- The gain is in thinly traded stocks: +1.21% a trade under ₹25 crore a day,
  +0.28% at ₹100–500 crore, -0.13% above ₹500 crore.
- It has shrunk: about +2.5% a trade in 2010–12, +0.5% to +0.8% in 2024–26.

Nothing trades on this. It needs minute bars (a fill a minute or more after
the open) before it counts as a pass or a fail.
