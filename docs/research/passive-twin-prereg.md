# M6 and G30 against their passive twins, after tax: plan written before looking at results

Written 2026-11-14, before downloading the index data or running anything.
Build 2 of [roadmap-2026-11.md](roadmap-2026-11.md).

M6 (monthly mid-cap momentum) and G30 (70% M6, 30% gold) are the two rules
that passed. Each has a cheap passive twin:

- **M6's twin:** a Nifty Midcap150 Momentum 50 index fund, which also holds
  mid-cap momentum stocks.
- **G30's twin:** 70% of that fund and 30% of a gold ETF.

The twins are taxed at 13% on long-term gains, not 20.8% on short-term ones,
and they need no monthly trading. This test asks one question: after costs
and tax, does trading M6 or G30 by hand beat simply holding its twin? It
compares existing rules with a benchmark, so it **adds no rules** to the
trial count (N stays 87).

## Data

- **M6:** monthly net returns exactly as arm A of `momentum_gold_study.py`
  (`m6_and_ew`): mid bucket, top 20 by 6-1 return, 0.40% per unit of
  turnover.
- **Gold:** GOLDBEES month-end closes, jump-adjusted, as in the gold study.
- **Momentum 50 fund:** the Nifty Midcap150 Momentum 50 **total return**
  index from niftyindices.com, at month-end, minus a 0.40% yearly fee taken
  monthly. Direct plans charge 0.24–0.5%, and 0.40% is the scorecard's
  figure.
  - If the total return index can't be downloaded, use the price index with
    no dividends. That understates the fund, and the doc will say so.
  - Before Aug 2022 the index is back-calculated with a method designed in
    2022, which flatters it. M6 is flattered too, because delisted stocks are
    missing. Neither bias is corrected here; build 3 deals with M6's.
- **Period:** Jan 2010 – Sep 2026, the same months as the gold study.

## Tax

- **M6, or the M6 sleeve:** 20.8% on each calendar year's net gain, with losses
  carried forward. This is `after_tax_cagr` in `weekly_rules_study.py`, the
  method behind M6's published +12.0%. In a mix, the M6 sleeve's tax is paid
  out of that sleeve each December, before rebalancing.
- **Fund and gold, or fund and gold sleeves:** 13% on gains.
  - A December rebalance that trims a sleeve taxes the realised gain at 13%,
    using average cost.
  - Whatever gain is left is taxed at 13% at the end, as if sold.
  - No ₹1.25 lakh exemption. It depends on the money involved, and leaving it
    out is harsher on the twins.
  - The first trim, in Dec 2010, is 11 months in and so strictly short-term.
    It is still taxed at 13%; this is a small simplification.
- **Mixes:** the twin and G30 go back to 70/30 at each December month-end,
  with the same moving costs as the gold study (0.40% on equity moved, 0.10%
  on gold moved).

## Windows

- **Full:** Jan 2010 – Sep 2026.
- **Live:** Sep 2022 – Sep 2026, after the index launched (17 Aug 2022), so
  no back-calculated index months.

The live window is only about 49 months. It is a check that the twin behaves
as the full window says, not a significance test.

## Decision rule (judged)

The twin is simpler, cheaper to run and better taxed, so a tie goes to the
twin.

1. **M6 against the fund.** M6 keeps a case for real money only if its
   after-tax CAGR beats the fund's by **at least 1 point a year in both the full
   and the live window**. Otherwise real money, if any, goes to the fund, and
   M6 stays a paper research trial.
2. **G30 against 70/30 fund and gold:** the same rule, on its own.

Passing does not approve real money. The October 2027 gates in
[momentum-paper-trial.md](momentum-paper-trial.md) and build 3, the
survivorship-free re-run, still apply. Failing changes no running trial;
the paper trial and the G30 shadow ledger carry on as booked.

**Reported for information, not judged:**
- the pre-tax CAGR and worst drawdown of every arm;
- discovery (2010–19) and holdout (2020–26) results;
- Nifty 50 buy-and-hold after 13%.

## Results (run 2026-11-14)

**Data:** the Total Returns Index from niftyindices.com (Reports →
Historical Data → Total returns Index Values), downloaded through the site's
own form in 18 one-year CSVs and merged into
`data/raw/indices/momentum50_tri.csv`.
- It runs from 1 Jan 2009 to 13 Nov 2026: 4,428 days, 243–252 a year, with no
  gap over 6 days.
- No month is missing for any arm.
- The script's self-check passed: its M6 after-tax figure equals
  `after_tax_cagr`.

| Arm | Window | Months | CAGR before tax | After tax | Worst drawdown |
|---|---|---|---|---|---|
| M6 | full | 201 | +15.4% | +12.5% | -60% |
| M6 | live | 49 | +31.4% | +25.1% | -26% |
| Momentum 50 fund | full | 201 | +21.4% | +20.5% | -31% |
| Momentum 50 fund | live | 49 | +16.2% | +14.5% | -24% |
| G30 | full | 201 | +16.2% | +13.5% | -34% |
| G30 | live | 49 | +32.1% | +26.3% | -15% |
| 70/30 fund + gold | full | 201 | +20.2% | +18.9% | -17% |
| 70/30 fund + gold | live | 49 | +21.1% | +18.9% | -14% |

For information only:

| Arm | 2010–19 after tax | 2020–26 after tax |
|---|---|---|
| M6 | +3.8% | +26.6% |
| Momentum 50 fund | +18.4% | +22.0% |
| G30 | +5.9% | +25.3% |
| 70/30 fund + gold | +16.0% | +22.4% |

**Verdict, by the rule as written: the twin wins both.**
- **M6 against the fund:** -8.0 points a year after tax over the full window,
  +10.7 over the live window. It needed +1 in both.
- **G30 against 70/30 fund and gold:** -5.5 full, +7.4 live.
- As fixed in advance, real money, if any, goes to the fund (or to 70/30 fund
  and gold). M6 and G30 stay paper research. The running paper trial and the
  G30 shadow ledger carry on as booked.

**What the verdict rests on, stated plainly:**
- **The two windows point opposite ways.** The full-window loss comes almost
  entirely from 2010–19, when the fund's history is back-calculated. Its
  +18.4% a year after tax there, against M6's +3.8%, is the flattering
  back-test this doc warned about before the run.
- **In the 49 months the index has been real, M6 beat it by 10.7 points a
  year after tax.** That is too short to rely on: it is one mid-cap boom, and
  M6 also carries the survivorship bias that build 3 couldn't measure.
- **So this test does not show the fund is better. It shows M6 hasn't proven
  it is better.** The rule sends a tie, or a contradiction like this one, to
  the simpler, better-taxed twin. That is why the verdict stands as written,
  not because the fund clearly won.
- **The fund also has the shallower drawdown in every window** (-31% against
  -60% over the full window). Unlike the returns, that point doesn't depend on
  the back-calculation.

Not re-run under any other rule. Any change to the bar would need a new
pre-registration and a ledger row.
