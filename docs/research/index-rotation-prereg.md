# Rotating between NSE index groups: plan written before looking at results

Written 2026-10-30, before running anything. Harsha asked whether the tests
covered the NSE index groups (size, sector, theme). The stock tests covered
size: every NSE stock, grouped by traded value at each date into large
(about the Nifty 100), mid (about the Midcap 150) and small (about the
Smallcap 250). They did not test sectors. This tests rotating between whole
index groups, which can be bought as ETFs.

## Data

eod2_data index files, which start on 21 Feb 2012. Price indices (no
dividends). Gaps are filled forward up to 5 sessions.

- **Sector set (13):** Nifty Auto, Bank, Energy, FMCG, IT, Media, Metal,
  Pharma, PSU Bank, Realty, Infrastructure, Commodities, India Consumption.
- **Size set (4):** Nifty 50, Nifty Next 50, Nifty Midcap 50, Nifty Smallcap 50.

## Rules

Signals use a close; the trade is at the **next session's close** (index files
have no tradable open), so nothing uses a price the rule could not have known.

| Rule | Set | Rebalance | Holds |
|---|---|---|---|
| **S1 Sector weekly momentum** | sectors | weekly | the 3 best by return from 68 to 5 sessions back |
| **S2 Sector last-week winners** | sectors | weekly | the 3 best by last 5-session return |
| **S3 Sector monthly momentum** | sectors | monthly | the 3 best by return from 6 month-ends to 1 month-end back |
| **Z1 Size monthly momentum** | size | monthly | the 1 best by the same 6-1 month return |

Equal weight. **Costs:** 0.15% a side (ETF fees, spread and STT at 0.001%;
several sector ETFs are thin, so this may be low). **Tax:** 20.8% STCG each
calendar year, losses carried forward.

## Baselines and bar

- Each rule against equal weight in its own set, rebalanced at the same
  frequency with the same costs; also the Nifty 50 and the monthly mid-cap M6
  over the same months.
- **Discovery:** Mar 2012 – Dec 2019. **Holdout:** Jan 2020 – Oct 2026.
- **Pass:** excess over own EW above 0 with t > 2 in discovery; above 0 and
  beating EW in at least 4 of 7 holdout years.
- **Worth using instead of the monthly stock picks:** also a higher after-tax
  CAGR in the holdout than the monthly mid-cap M6 and than the Nifty 50.
- Four rules tried; one may pass by luck.

## Results (run 2026-10-30)

`scripts/research/index_rotation_study.py`, Mar 2012 to Oct 2026, net of costs.

| Rule | CAGR | After tax | Worst drawdown | Own EW CAGR | Discovery excess (t) | Holdout excess (t), years | Verdict |
|---|---|---|---|---|---|---|---|
| S1 Sector weekly momentum | +9.9% | +7.9% | -52% | +11.9% | -0.07%/wk (-0.9) | +0.02%/wk (+0.3), 4/7 | fail |
| S2 Sector last-week winners | -1.0% | -1.2% | -64% | +10.9% | -0.24%/wk (-3.3) | -0.18%/wk (-2.4), 2/7 | fail |
| S3 Sector monthly momentum | +8.9% | +7.2% | -50% | +11.9% | -0.02%/mo (-0.1) | -0.34%/mo (-1.0), 3/7 | fail |
| Z1 Size monthly momentum | +13.3% | +10.6% | -40% | +14.2% | -0.00%/mo (0.0) | -0.08%/mo (-0.3), 4/7 | fail |

Nifty 50 over the same span: +10.3% a year. **None passes.** Picking the
recent best sectors or size group did worse than holding all of them equally;
chasing last week's best sectors lost money. Momentum works across stocks in
the mid-cap group, not across whole sectors or size groups.
