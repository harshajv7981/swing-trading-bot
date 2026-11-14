# Sample backtest audit report

*A sample built from one of my own strategies, so no client is named. It
shows method and findings only, with no return figures, in line with
SEBI's 2025 rules on educational content. It is not investment advice.*

---

**Strategy:** monthly mid-cap momentum. It holds the 20 NSE mid-caps with
the strongest 6-month return, skipping the latest month, and rebalances
monthly to equal weight.
**Backtest supplied:** daily NSE prices from 2010 onward, with discovery
(2010–19) and holdout (2020 onward) periods, after 0.40% per unit of
turnover.
**Question asked:** is the backtest's edge real enough to put money behind?

## Summary

The backtest was built carefully: there is no look-ahead, costs are
charged, and the rule was fixed before testing. It still overstates the
case for trading it.
- The edge is smaller than it looks once the number of variants tried is
  counted.
- Delisted stocks are missing from the data, which flatters a mid-cap
  strategy.
- After Indian tax, the rule failed the pre-set comparison with a cheap index
  fund that holds the same kind of stocks.

**Verdict on the question asked:** the evidence doesn't support trading the
rule over holding the fund.

## Checks

| # | Check | Finding | Severity |
|---|---|---|---|
| 1 | **Look-ahead:** does any signal use data not known at the time? | None found. Ranks use month-end closes only, and the latest month is skipped | Pass |
| 2 | **Rule fixed in advance:** was the rule set before the results were seen? | Yes. It was pre-registered, with a discovery and holdout split | Pass |
| 3 | **Overfitting:** is the edge real once all the variants tried are counted? | 87 related variants had been tried. The deflated Sharpe ratio is 0.78, short of the usual 0.95 bar. Every neighbouring setting also beat the baseline, so the edge is broad, but its size is likely overstated by about half | Medium |
| 4 | **Survivorship:** are delisted stocks in the data? | No. Fully delisted companies are absent. A survivorship-adjusted market factor (IIMA) confirms that the momentum effect itself is real, but it tracks this rule too loosely to size the bias. **Not measured**, and it can't be without licensed data | High (unquantified) |
| 5 | **Costs:** are costs charged on real turnover? | Yes, about 50% turnover a month at 0.40%. Real fill prices are not yet observed; a forward fill-shortfall log was added to measure them | Low |
| 6 | **Indian tax:** is tax applied the way it would really fall? | Nearly every gain is short-term at 20.8%, against 13% long-term for a fund held over a year. This alone takes a large share of the edge | High |
| 7 | **Cheapest real alternative:** does an index fund do the same job? | Against a Momentum 50 index fund after tax, the rule failed the pre-set bar. It trailed over the full history but led over the shorter period since the index launched. The full-history result relies partly on the index's back-filled data, which flatters it | High |
| 8 | **Cheap fixes:** does a turnover buffer rescue it? | No. A pre-registered rank buffer failed its bar in the discovery period | Info |

## What the evidence supports

- Indian mid-cap momentum is a real effect, and this rule captures it.
- **It does not support** trading the rule rather than holding the
  matching index fund, after costs and tax.
- **It does not support** any expected return figure. Survivorship bias is
  unmeasured, and the edge is likely overstated.

## Recommended next steps

1. **Keep the rule as a forward paper test,** with its review date and
   pass/fail criteria already written down. Don't change the rule
   mid-test.
2. **Measure real fill prices for three months** before trusting the cost
   assumption.
3. **If survivorship matters to the decision,** buy a licensed price history
   that includes delisted stocks, and re-run the rule unchanged against the
   pass bar already written.
4. **Write any real-money limits down in advance:** a capital cap, a review
   point after a fall, and a plan for a 60% fall.

## Scope and limits

This audit checks how the backtest was built. It doesn't predict future
returns, and it isn't a recommendation to buy, sell or hold anything. A
strategy can pass every check here and still lose money. All trading
decisions stay with the strategy's owner.
