# Stop-losses on M6: plan written before looking at results

Written 2026-11-17, before running anything. This is the one test kept from the
AI-roadmap review of the same day. The roadmap's TFT, offline RL, HMM regimes
and SEBI RA registration were cut.

The adviser brief prints a stop under every pick: the close minus 2 × the
14-day Wilder ATR (`adviser_brief.stop_levels`). The brief calls these
**information only**, because the tested M6 rule has no stop and exits only at
the month-end re-rank. This study checks whether a stop would have helped M6,
before anyone relies on one.

**Trial count:** this adds **2 momentum trials (N 92 → 94)**, one for each
judged stop arm. The multiples 2 and 3 are fixed here. No other multiple,
ATR length, trailing variant or quantile stop will be tried.

## Window

The last 60 booked month-ends, **Nov 2021 – Oct 2026**, under the five-year
rule of 2026-11-16 (see the [trials ledger](trials-ledger.md)). There is no
discovery/holdout split. The out-of-sample check is the forward paper record.
This window is a single regime, the mid-cap boom, so a pass shows less than it
would over a longer one.

## Data and rule

Everything not listed here is as `m6_and_ew` in `momentum_gold_study.py`:
- eod2_data, EQ series;
- the mid bucket: ranks 101–250 by 60-day median traded value, with at least
  ₹5 crore;
- 6-1 month momentum, top 20, equal weight, rebalanced monthly;
- 0.40% per unit of one-way turnover.

- **A, M6 (baseline):** no stop. This is the rule the paper trial runs.
- **S2 (judged):** at each month-end, every holding, new or kept, gets
  stop = that day's close − 2 × ATR(14), Wilder, from the raw daily OHLC. This
  is the same level the brief prints.
  - On each later day of the month the stop is checked with
    `paper.ledger.stop_fill`. If the open is at or below the stop, the sale
    fills at the open (a gap). If only the low reaches it, the sale fills at
    the stop.
  - A stopped name pays 0.40% on its weight, and that money sits in cash at 0%
    until the month-end.
  - Re-buying it at the month-end, if it still ranks, counts as normal
    rebalance turnover.
  - The stop is not moved during the month.
- **S3 (judged):** as S2, with 3 × ATR(14).

If a stock has no ATR because it has fewer than 15 bars, it gets no stop that
month.

## Tax

20.8% on each calendar year's net gain, with losses carried forward
(`after_tax_cagr`), the same for every arm.

## Pass bar (judged)

A stop exists to cut losses, so it is judged on drawdown, with a cap on what it
may cost. A stop arm passes only if **both** of these hold:

1. **Its worst month-end drawdown is at least 5 points shallower than A's.**
   For example, -28% against A's -33%.
2. **Its after-tax CAGR is no more than 1.0 point a year below A's.**

If both arms pass, S3 is chosen if its after-tax CAGR is higher. Otherwise S2
is chosen.

**If one passes:**
- The brief's stop note changes from "never tested" to name the arm and this
  result.
- The paper trial and its gate stay exactly as booked, with M6 and no stop.
- Making the stop part of the traded rule needs Harsha's approval and a new
  paper record.

**If neither passes:** the brief keeps the stops as information only, and its
note says that 2× and 3× ATR stops were tested and failed. No other stop is
tried.

## Reported for information, not judged

- The share of holdings stopped each month, and how many of those were gaps
  through the stop.
- The average return of stopped names from the stop to the month-end. This
  shows whether the stop sold before recoveries.
- The worst single month for each arm.
- Each arm against the Midcap150 Momentum 50 index over the same 60 months.

## Results (run 2026-11-17)

`scripts/research/m6_stop_loss_study.py data/raw/eod2_data/daily`, eod2_data to
13 Nov 2026. The window is the 60 months ending 30 Nov 2021 – 30 Oct 2026. Its
self-check passed: with no stop, the arm reproduces `m6_and_ew`'s monthly
returns exactly.

| Arm | CAGR | After tax | Worst drawdown | Worst month | Holdings stopped/month | Gaps through stop | Stopped name, fill to month-end |
|---|---|---|---|---|---|---|---|
| A, M6 (no stop) | +25.0% | +20.1% | -24.9% | -13.4% | – | – | – |
| S2 (2× ATR) | +9.2% | +7.2% | -33.0% | -11.4% | 46% | 52 of 556 | +2.2% (median +1.3%) |
| S3 (3× ATR) | +11.1% | +8.7% | -28.7% | -14.1% | 27% | 34 of 324 | +3.7% (median +2.3%) |
| Momentum 50 fund (info) | +13.9% | +12.4% (held, long-term tax) | -24.2% | -11.8% | – | – | – |

**Verdict: FAIL for both stops.**
- **Drawdown:** S2's worst fall was 8.1 points deeper than M6's, and S3's was
  3.8 points deeper. Each needed to be 5 points shallower.
- **Cost:** after tax, S2 gave up 12.9 points a year and S3 gave up 11.4. The
  cap was 1.0.

The stops sold too often: almost half of S2's holdings were stopped each month.
On average, a stopped name then rose a further 2–4% by the month-end, so the
stop sold before the recovery. Selling also paid costs, and the money sat in
cash. Stops stay information only in the adviser brief, and no other stop will
be tried. The paper trial is unchanged.
