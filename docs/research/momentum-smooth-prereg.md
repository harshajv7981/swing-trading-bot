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
