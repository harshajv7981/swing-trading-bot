# Mid-cap momentum plus gold: plan written before looking at results

Written 2026-10-29, before running anything. Harsha said yes to testing whether
holding gold next to mid-cap M6 cuts its drawdowns. Three in-strategy fixes
already failed:
- a trend filter and quarterly rebalancing ([momentum-drawdown-prereg.md](momentum-drawdown-prereg.md));
- low-volatility mixes ([momentum-lowvol-prereg.md](momentum-lowvol-prereg.md)).

This test mixes in a separate asset instead. Gold ended 8 of the 9 Nifty 50
falls of 15% or more since 2006 flat or up. For example:
- 2008: +5% while the Nifty fell 60%;
- Nov 2010 – Dec 2011: +14% while it fell 27%.

The source is Value Research, "Gold: safe haven in equity crashes?". Mixing in
gold is a portfolio choice, not a new edge. So the bar asks for better return
per unit of risk, not only a smaller drawdown, which any cut in equity would give.

## Data

- **Mid-cap M6:** the paper trial's rule, computed exactly as arm A of
  `momentum_lowvol_study.py`. That means the mid bucket (ranks 101–250 by
  traded value, rebuilt monthly), the top 20 by 6-1 month return, monthly
  rebalancing with drifting weights, and 0.40% cost per unit of turnover.
- **Gold:** the GOLDBEES ETF close in eod2_data, jump-adjusted. This fixes the
  100:1 split of 19 Dec 2019. Month-end returns are used, and the ETF's own fee
  is already inside its price.
- **Debt:** a liquid fund at 6% a year, as in the earlier studies.
- **Period:** Jan 2010 – Sep 2026, split the same way:
  - **Discovery:** 2010–19.
  - **Holdout:** 2020–26.

## Arms

All mixes are rebalanced back to their target weights at each December
month-end and drift in between. Moving money between sleeves costs:
- 0.40% on the equity moved;
- 0.10% on the gold moved (an ETF trade, with no STT on gold ETFs).

- **A:** 100% M6.
- **G30 (judged):** 70% M6, 30% gold. This is the one mix fixed in advance.
- **For information only, not eligible:**
  - G20 (80/20) and G40 (60/40);
  - 60% M6, 20% gold, 20% liquid fund;
  - 100% gold;
  - the mid-bucket EW.

## Pass bar

G30 passes only if all three hold:

1. **Drawdown:** its worst drawdown on month-end values, over Jan 2010 –
   Sep 2026, is shallower than A's by at least a third.
2. **Better return per risk:** its Sharpe ratio (monthly returns, against 6%
   cash) is above A's in **both** discovery and holdout.
3. **Still beats the baseline:** its CAGR is above the mid-bucket EW's CAGR in
   **both** discovery and holdout.

Tax is not part of the bar. Yearly rebalancing sells gold held 12 months,
which is taxed as a long-term gain at 12.5%, below the 20% on equity. So a
pre-tax comparison slightly understates G30.

## Known limits

- **Gold's recent run is extreme.** Gold rose sharply in 2025, which flatters
  the holdout.
- **No dividends.** The equity sleeve is measured without dividends, as before.
- **No 2008.** The data starts in 2010, although gold's best-known crash rescue
  was in 2008.

## What follows from the result

- **The paper trial's stock picks do not change either way.**
- **If G30 passes:** the trial adds a 30% gold sleeve as a shadow ledger, and the
  October 2027 review compares the two.
- **If it fails:** the drawdown stays a question of how much money goes into the
  strategy.

## Results (run 2026-10-29 on the Mac, eod2_data to 23 Oct 2026)

`scripts/research/momentum_gold_study.py`, Jan 2010 – Sep 2026, 201 months.
Over the whole period, the correlation of monthly returns with gold was -0.13
for both M6 and the mid-bucket EW.

| Mix | CAGR | Worst drawdown | Sharpe | 2010–19 CAGR / Sharpe | 2020–26 CAGR / Sharpe |
|---|---|---|---|---|---|
| Mid-bucket EW | +7.8% | -62.6% | 0.19 | +0.2% / -0.09 | +20.2% / 0.65 |
| A, 100% M6 | +14.9% | -61.7% | 0.43 | +4.6% / 0.09 | +32.2% / 0.98 |
| **G30, 70/30 (judged)** | **+16.0%** | **-35.1%** | **0.57** | +7.3% / 0.16 | +30.2% / 1.20 |
| G20, 80/20 (info) | +15.8% | -44.1% | 0.52 | +6.6% / 0.13 | +31.0% / 1.12 |
| G40, 60/40 (info) | +16.0% | -25.5% | 0.63 | +7.8% / 0.18 | +29.3% / 1.27 |
| 60/20/20 with cash (info) | +14.4% | -29.1% | 0.55 | +7.2% / 0.15 | +25.9% / 1.16 |
| Gold alone (info) | +12.7% | -24.4% | 0.48 | +7.6% / 0.18 | +20.5% / 0.88 |

**Verdict: G30 passes all three checks.**
- **Drawdown:** -35.1%, against a bar of -41.2%.
- **Sharpe:** above A in both periods.
- **CAGR:** above the mid EW in both periods.

Its worst year is 2011, at -26% against M6's -50%. Gold rose 30% that year.

**How much of this is luck:**
- **Gold had two big years.** It rose 30% in 2011 and 72% in 2025. Over 2010–26
  it made 12.7% a year, which is high for gold. If gold only keeps pace with
  inflation, the mix would lose its CAGR lead. The drawdown cut depends less on
  gold's level than on gold moving against mid caps, so we expect it to hold
  better. That is a reasoned guess, not something this test shows.
- **Gold falls too.** It lost money in 2013–15 (-6%, -9%, -8%), while momentum
  was mixed.
- **The result does not hinge on the 30% weight.** Every gold weight tried
  (20%, 30%, 40%) lowered the drawdown and raised the Sharpe.
- **Tax was left out,** and it slightly favours the mix (12.5% long-term tax on
  gold sold each December).

**Next step, as planned:** the paper trial gains a 70/30 gold shadow ledger
(`swingbot-momentum report`, "70/30 with gold since start"). The October 2027
review judges both.
