# Kronos forecasting model on NSE stocks: plan written before looking at results

Written 2026-10-30, before running anything. Harsha asked to test Kronos
(github.com/shiyu-coder/Kronos), a pretrained "foundation model" for price
candles. It reads a stock's recent daily open/high/low/close/volume and
forecasts the next few candles. This tests whether its forecasts pick better
stocks for the coming week than the simple rules. Data, universe and costs as in
[weekly-rules-prereg.md](weekly-rules-prereg.md).

## Why the window is short

Kronos was pretrained on market data up to roughly mid-2025, so any earlier
period may already be in its memory. The only fair window is the weeks with
signals from **1 Sep 2025 to the last full week of Oct 2026** (about 60 weeks).
That is too short to prove an edge with confidence; it can only show a clear
failure or a promising sign worth a forward paper trial.

## The rule (K-W)

| | |
|---|---|
| Model | `NeoQuasar/Kronos-small` with `NeoQuasar/Kronos-Tokenizer-base`, CPU, weights as published |
| Universe | Liquid 250 (top 250 by 60-day median traded value, at least 260 days of history and Rs 5 crore a day) |
| Signal | last trading day of each week, using data to that close only |
| Input | the last 400 daily candles (open, high, low, close, volume, traded value) |
| Forecast | next 5 trading days; T 1.0, top_p 0.9, 1 sample (Kronos's own predictor and default), torch seed 0 |
| Score | forecast close on day 5 / last close - 1 |
| Holds | top 20 by score, equal weight |
| Trades | next open, held to the open after the next signal, 0.25% a side |

## Bar (all three needed)

1. **Ranking skill:** the weekly rank correlation between Kronos's score and the
   stock's actual next-week return (over all 250) averages above 0 with t > 2.
2. **Beats equal weight:** K-W's mean weekly return after costs is above Liquid
   250 equal weight (same weeks, same costs).
3. **Beats the paper trial's rule:** K-W's net growth over the window is above
   monthly mid-cap M6 (top 20 by 6-1 month return in stocks 101-250, month-end
   close, 0.20% a side) over the same dates.

Amended before any run: the first draft said 5 samples averaged. Timed on the
4-core cloud machine with random weights, that costs about 5.5 s per stock-week
(about 23 hours for the window), so it is 1 sample (0.75 s, about 3 hours).
A single sample is noisier, which can only hide skill, not invent it.

## Results (run 2026-10-30)

`scripts/research/kronos_study.py`, 58 weekly signals from 5 Sep 2025 to 9 Oct
2026 (data ends 23 Oct 2026), net of costs.

| | Growth | Worst drawdown |
|---|---|---|
| **Kronos top 20** (weekly) | **-16.9%** | -23.3% |
| Liquid 250 equal weight (weekly) | +3.7% | -12.8% |
| Monthly mid M6 (Sep 2025 to Sep 2026) | +22.9% | -13.2% |

1. **Ranking skill: fail.** Rank correlation averaged -0.009 (t -0.6), and
   was positive in 45% of weeks. Kronos's forecasts had no link to next week's
   returns.
2. **Beats equal weight: fail.** -0.37% a week after costs (t -2.7), ahead in
   34% of weeks. Random picks lost about 0.5% a week on the same test, mostly
   weekly trading costs, so Kronos did about as well as random picks.
3. **Beats M6: fail.** -16.9% vs +22.9%.

Kronos is dropped. The paper trial's rule stays.

A pass means a forward paper shadow trial, not money: 60 weeks is too few to
trust. A fail means Kronos is dropped. Nothing (lookback, samples, top-N) is
tuned after seeing results; any extra look is labelled exploratory.
