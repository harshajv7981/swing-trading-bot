# Multi-month momentum: plan written before looking at results

Written 2026-10-29, before running anything. The swing rules and the news calls
showed no edge ([price-rules-prereg.md](price-rules-prereg.md)). Momentum is the
most studied anomaly in equities: stocks that rose most over the past 6–12
months keep beating the rest for a few months (Jegadeesh & Titman 1993). The
effect has been reported in Indian stocks too. This tests whether it holds on our
universe after costs. It is a monthly portfolio strategy, not a swing trade, so
it would be a different product from the current bot.

## Data

- The same eod2_data daily closes as the price-rule study (104 current Nifty 100
  stocks, split-adjusted), plus NIFTYBEES.
- Prices only, so no dividends. That understates every portfolio about equally.
- **Survivorship bias:** today's index members look better in hindsight. This
  flatters every portfolio built from them, so each one is judged against an
  equal-weight portfolio of the same stocks, not against zero or the index.

## Portfolios

All rebalance on the last trading day of each month and hold until the next one.
- **Eligible:** a stock with at least 260 sessions of history on the rebalance day.
- **Costs:** 0.25% on the value traded each rebalance (round trip 0.25% per
  name swapped).

The portfolios:
- **EW (baseline):** equal weight in every eligible stock.
- **M12:** the 10 eligible stocks with the best return from 12 months back to 1
  month back (skipping the latest month), equal weight.
- **M6:** the same, but over 6 months back to 1 month back.
- **M12F:** M12, but all cash (0% return) for any month that starts with
  NIFTYBEES below its 200-day average.

## Split and pass bar

- **Discovery:** monthly returns from Jan 2010 to Dec 2019. **Holdout:** Jan 2020
  to the last full month (Sep 2026). Nothing is changed after seeing discovery.
- Measured on the monthly excess return over EW, after costs.
- **Discovery pass:** mean monthly excess above 0 with a t-stat above 2.
- **Holdout pass (the one that counts):** mean monthly excess above 0, and the
  portfolio beats EW in at least 4 of the 7 holdout calendar years (2020–2026,
  2026 being partial).
- A portfolio must pass both periods. With 3 tried, one may pass by luck.
- Also reported, not part of the bar: CAGR, the worst drawdown, and turnover.

## Results (run 2026-10-29, after the plan above was committed)

`scripts/research/momentum_study.py` covers month-ends from Jan 2010 to Sep 2026.
Excess is the portfolio's monthly return minus EW's, after costs.

| Portfolio | CAGR 2010–26 | Worst drawdown | Discovery 2010–19 excess (t) | Holdout 2020–26 excess (t) | Holdout years beating EW | Verdict |
|---|---|---|---|---|---|---|
| EW baseline | +19.0% | -29.5% | | | | |
| M12 (12-1) | +27.8% | -32.1% | +0.58%/mo (t 1.9) | +0.75%/mo (t 1.6) | 5/7 | fail (discovery t under 2) |
| **M6 (6-1)** | +26.7% | -31.9% | +0.72%/mo (t 2.6) | +0.47%/mo (t 0.9) | 5/7 | **pass** |
| M12F (with market filter) | +22.6% | -17.4% | +0.10%/mo (t 0.2) | +0.37%/mo (t 0.6) | 5/7 | fail |

M6's excess over EW year by year:
- 2010–19: +5, +11, -1, +11, +17, +8, +6, +10, -4, +23%.
- 2020–26: +19, +16, +15, +9, +12, -17, -17% (2026 to September).

M6 turns over 39% a month, about 4 of the 10 names.

**What the pass means, and what it doesn't:**
- **M6 passes the plan as written.** It is the only one of the four tests in either study that does. It beat the same stocks held equally in 8 of 10 discovery years and 5 of 7 holdout years. M12 behaves almost the same and misses the discovery bar only narrowly (t 1.9). So this looks like one effect, not luck in one setting.
- **The holdout evidence is weak on its own** (t 0.9), and **the last two years were bad**: -17% versus EW in both 2025 and 2026. Momentum is known for sharp losing stretches when market leadership flips.
- **Survivorship bias flatters momentum more than it flatters EW.** Today's Nifty 100 holds the stocks that kept rising. Momentum buys the risers, and the risers that later crashed out of the index are missing from this data. Some of the excess is likely this bias, and these files can't measure how much. That needs historical index membership.
- **The market filter (M12F) cut the worst drawdown from -32% to -17%,** but it also gave up most of the excess. It is reported, not chosen.

**Next step:** a forward paper trial of M6 from November 2026. It rebalances once a month with no model calls, so it costs no tokens. It would run beside the risk rules, not through the swing ticket. Holdings and results get scored against EW each month, and real money only after at least 12 months of paper results.
