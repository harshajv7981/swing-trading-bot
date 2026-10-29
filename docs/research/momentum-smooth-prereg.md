# Smooth mid-cap momentum (frog in the pan): plan written before looking at results

Written 2026-10-29, before running anything. Harsha said yes to testing whether
preferring stocks that rose steadily, over ones that rose in a few jumps,
improves mid-cap M6.

**The idea.** Investors under-react to news that arrives in many small pieces,
so gains made bit by bit keep going longer than gains from one jump.
- **The measure** is information discreteness (ID), from Da, Gurun & Warachka
  (2014), "Frog in the pan":
  - ID = sign(past return) × (share of down days − share of up days).
  - A low ID means a smooth, continuous move.
- **Outside the US,** Goyal, Jegadeesh & Subrahmanyam (Review of Finance)
  find this limited-attention story explains momentum best, including in
  emerging markets.

**What we already know.** Our "calm momentum" arm, momentum within the calmer
half ([momentum-lowvol-prereg.md](momentum-lowvol-prereg.md)), is related but
not the same. It passed discovery (t 2.2) and was flat in the holdout.

## Data, universe and costs (unchanged)

- **Prices:** eod2_data daily bars, EQ series, jump-adjusted
  (`swingbot.momentum.load`), without dividends.
- **Universe:** the mid bucket, rebuilt at each month-end. It is ranks 101–250
  by 60-session median traded value, among stocks with at least 260 sessions
  and a median value of at least ₹5 crore.
- **Rebalancing:** monthly, with weights drifting in between.
- **Cost:** 0.40% on each unit of one-way turnover.
- **Period:** Jan 2010 – Sep 2026, split the same way:
  - **Discovery:** 2010–19.
  - **Holdout:** 2020–26.

## Arms

- **A, baseline:** the top 20 by M6 (return from 6 months back to 1 month back),
  equal weight. This is the paper trial's rule.
- **S, smooth (the only judged arm):**
  - Take the top 40 by M6.
  - Keep the 20 with the lowest ID, equal weight.
  - ID uses daily returns over the same window as M6, from the month-end 6
    months back to the month-end 1 month back. Days without a price are left
    out, and at least 80 valid days are needed.
- **S + 30% gold (information only):** S held as 70% next to 30% GOLDBEES,
  rebalanced each December. It is built exactly as in
  [momentum-gold-prereg.md](momentum-gold-prereg.md), for comparison with the
  70/30 shadow already in the trial.

## Pass bar

S passes only if both hold:

1. **Beats the mid-cap average.** This is the same bar as the earlier momentum
   studies:
   - **Discovery:** mean monthly excess over the mid-bucket EW above 0, with a
     t-stat above 2.
   - **Holdout:** mean monthly excess above 0, and beating EW in at least 4 of
     the 7 holdout years.
2. **Improves on today's rule.** Its mean monthly return minus A's is above 0
   in **both** discovery and holdout.

Also reported:
- CAGR, worst drawdown, Sharpe (against 6% cash) and turnover for A, S and
  S + gold;
- S minus A by year, with the t-stat in each period.

## What follows from the result

- **The paper trial does not change either way.**
- **If S passes:** it runs as a second shadow ledger in the trial from the next
  month-end, and the October 2027 review compares it with A. A pass here is
  not a reason to switch the trial mid-way.
- **If it fails:** we keep plain M6, and smoothness is dropped as a filter.

## Results (run 2026-10-29 on the Mac, eod2_data to 23 Oct 2026)

`scripts/research/momentum_smooth_study.py`, Jan 2010 – Sep 2026.

| Portfolio | CAGR | Worst drawdown | Sharpe | 2010–19 CAGR / Sharpe | 2020–26 CAGR / Sharpe | Turnover/month |
|---|---|---|---|---|---|---|
| Mid EW | +7.8% | -62.6% | 0.19 | +0.2% / -0.09 | +20.2% / 0.65 | |
| A, M6 | +14.9% | -61.7% | 0.43 | +4.6% / 0.09 | +32.2% / 0.98 | 51% |
| **S, smooth** | +15.7% | -62.1% | 0.47 | +5.7% / 0.12 | +32.3% / 1.01 | 53% |
| A + 30% gold | +16.0% | -35.1% | 0.57 | +7.3% / 0.16 | +30.2% / 1.20 | |
| S + 30% gold (info) | +16.5% | -35.5% | 0.62 | +8.0% / 0.19 | +30.3% / 1.24 | |

**S against the mid EW:**
- **Discovery:** +0.46% a month (t 2.5).
- **Holdout:** +0.84% a month (t 2.1), beating EW in 6 of 7 years.

**S minus A:**
- **Discovery:** +0.05% a month (t 0.3).
- **Holdout:** -0.03% a month (t -0.1).
- **By year:** the difference swings from -21% (2023) to +18% (2020) with no
  pattern.

**Verdict: fail.** S beats the mid-cap average, but it is no better than plain
M6 in either period. The extra half point of CAGR is noise. Smoothness is
dropped as a filter, and the paper trial keeps plain M6.
