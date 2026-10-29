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
