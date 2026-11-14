# Smart-money market structure rules: plan written before looking at results

Written 2026-11-14, before running anything. Harsha asked whether the
smart-money-concepts package (github.com/joshyattridge/smart-money-concepts)
can be used. Its `swing_highs_lows` marks a swing on the pivot bar using the 25
bars after it (`shift(-(swing_length // 2))`), and its break-of-structure,
order-block, liquidity and retracement functions are all built on that, so any
backtest with it sees the future. This study uses our own detector instead:
`swingbot.swings.confirmed_swings`, which stamps each swing on the bar it became
known (tested in `tests/test_swings.py`, including that no row changes when
later bars are added).

## Swings

`confirmed_swings(bars, k=5)` on daily bars: a swing high is a high above the
5 sessions on each side, known 5 sessions after the pivot. Swing lows mirror it.
k = 5 is fixed here. No other value will be run.

## Data, universe, timing, costs: unchanged from weekly-rules

Everything is the same as [weekly-rules-prereg.md](weekly-rules-prereg.md) and
[book-rules-prereg.md](book-rules-prereg.md): eod2_data daily bars, the Liquid
250 universe (U) rebuilt each week from data known that day, signal at the
week's last close, trade at the next open, hold to the open after the next signal
day, 0.25% a side, 20.8% STCG each year, same survivorship caveat.

## Rules (long only, Liquid 250, state read at each signal day)

Each rule holds **every U stock whose state is long, at equal weight, capped at
10% each, rest in cash at 6%**, as in book-rules. "Latest swing" means the most
recent swing confirmed on or before the signal day.

| Rule | SMC name | Long state at the signal day |
|---|---|---|
| **S1 Structure up** | Bullish market structure | The two latest swing highs are rising AND the two latest swing lows are rising |
| **S2 Break of structure** | BOS | Goes long when a daily close is above the latest swing high; goes flat when a daily close is below the latest swing low; otherwise keeps its state |
| **S3 Change of character** | CHoCH | Goes long when a daily close is above the latest swing high while the two latest swing highs were falling (structure was down); goes flat when a daily close is below the latest swing low |

State machines (S2, S3) are updated on every daily close; the portfolio only
reads them at the weekly signal day.

**Not tested, and why:** fair value gaps and order blocks are entries on a
retrace into a zone, which needs a limit fill inside the day that daily bars
can't confirm (the same problem that sank G-down in
[intraday-open-close-prereg.md](intraday-open-close-prereg.md)). Liquidity
sweeps and sessions need intraday bars.

## Baselines

- **EW U:** equal weight in all of U, reset weekly (the rules are judged against this).
- **B5 Channel** from book-rules: the closest rule already tested (Donchian
  20/10 breakout), to show whether swings add anything over a plain channel.
- **Monthly mid M6** and **Nifty 50** buy and hold.

## Pass bar (same as weekly-rules and book-rules)

- **Discovery** (weeks ending 2010–2019): mean weekly excess over EW U, after
  costs, above 0 with t > 2.
- **Holdout** (2020 to the end of the data): mean weekly excess above 0, and
  beating EW U in at least 4 of the holdout calendar years.
- **Worth switching to:** passes both, AND its holdout after-tax CAGR beats the
  monthly mid M6 and the Nifty 50.
- **Multiple testing:** 21 price rules already failed or were not trusted
  (price rules A–D, weekly W1–W9, book B1–B5, intraday G-up, G-down, H20), and
  this is the 19th study in [trials-ledger.md](trials-ledger.md). Adding 3 makes
  24 price rules. A single pass at t ≈ 2 is about what luck alone gives across
  24 tries, so any pass is
  reported as "candidate for a forward paper trial", never as proven, and
  nothing is wired into the bot from this doc.

## Known limits

Swing points from adjusted prices can move a little when a later split or
dividend adjustment rescales history. Survivorship: eod2_data lacks fully
delisted stocks.

## Results (run 2026-11-14, after the plan above was written)

`scripts/research/smc_structure_study.py data/raw/eod2_data/daily`, 880 weeks to
9 Nov 2026. Net of 0.25% a side; "after tax" is after 20.8% STCG each year
(Nifty: 13% LTCG at the end). Check on the code: B5 Channel (+1.2%), EW U (+9.2%)
and the monthly M6 (+14.8%) match book-rules exactly.

| Portfolio | CAGR | After tax | Worst DD | Sharpe | Turnover/wk | Avg names | 2020+ after tax |
|---|---|---|---|---|---|---|---|
| S1 Structure up | +0.7% | +0.5% | -66% | -0.14 | 31% | 72 | +8.0% |
| S2 Break of structure | +4.3% | +3.4% | -56% | 0.02 | 19% | 113 | +10.3% |
| S3 Change of character | +3.1% | +2.4% | -57% | -0.04 | 19% | 94 | +9.7% |
| B5 Channel | +1.2% | +0.8% | -57% | -0.14 | 28% | 84 | +6.1% |
| EW U | +9.2% | +7.4% | -53% | 0.25 | 3% | 207 | +14.5% |
| Monthly mid M6 (trial) | +14.8% | +12.0% | -59% | 0.44 | | 20 | +24.9% |
| Nifty 50 buy & hold | +10.7% | +9.9% | -35% | | | | +10.0% |

| Rule | Discovery 2010–19 | Holdout 2020+ | Holdout years beating EW | Verdict |
|---|---|---|---|---|
| S1 Structure up | -0.17%/wk (t -4.4) | -0.13%/wk (t -3.4) | 0/7 | fail |
| S2 Break of structure | -0.10%/wk (t -2.6) | -0.09%/wk (t -2.2) | 0/7 | fail |
| S3 Change of character | -0.13%/wk (t -3.3) | -0.10%/wk (t -2.4) | 1/7 | fail |
| B5 Channel (comparison) | -0.14%/wk (t -3.1) | -0.18%/wk (t -3.1) | 0/7 | fail |

**No rule passes.** All three trail plain equal weight in almost every year,
before and after 2020, by about 5–10% a year. S2 and S3 lose less than the
channel rule, so swings are a slightly better trend filter than a 20/10 channel,
but timing in and out of liquid stocks on market structure still costs more
than it saves. Nothing is wired into the bot.
