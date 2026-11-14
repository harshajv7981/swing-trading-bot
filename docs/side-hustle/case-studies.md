# Two case studies: what a careful backtest audit catches

Draft written 2026-11-14 for build 5 of
[roadmap-2026-11.md](../research/roadmap-2026-11.md). These describe
**methods and findings**: they make no return claims and recommend nothing.
Before publishing them, check that any figure added later uses price data at
least three months old, as SEBI's 2025 rules require for educational content.

## 1. A strategy that passed its backtest, then lost to an index fund

**Setup:** a monthly mid-cap momentum rule had passed a pre-registered
backtest on NSE data, with discovery and holdout periods and full costs. It
was running as a forward paper trial.

**The question nobody had asked:** does it beat the cheap fund that holds
the same kind of stocks?
- A Nifty Midcap150 Momentum 50 index fund holds the same style of stocks.
- It costs a fraction of a percent a year.
- Held over a year, its gains are taxed at the long-term rate. The trading
  rule pays the higher short-term rate on almost every gain.

**What the audit did:**
- Wrote the decision rule down first. The rule had to beat the fund by a
  margin after tax, in both the full history and the period since the index
  launched.
- Rebuilt both sides with Indian tax applied the way each would really be
  taxed:
  - yearly short-term tax on the trading rule, with losses carried forward;
  - long-term tax on the fund, at sale.
- Flagged in advance that the index's pre-launch history is back-calculated.

**What it found:**
- The two windows disagreed. Over the long history, which is mostly the
  back-calculated index, the fund won clearly.
- Since launch, the trading rule did better, but over only a few years in one
  mid-cap boom.
- By the rule written beforehand, the fund wins.
- The lesson isn't "funds always win". It is that the honest comparison
  needs after-tax figures, the cheapest real alternative, and a rule fixed
  before the result is seen.

**What a client gets from this:** a check they almost never run on their
own strategy, done with Indian tax rules, plus a plain statement of what the
evidence does and doesn't support.

## 2. Measuring survivorship bias without paying for data

**Setup:** the same rule's backtest used a free price history that drops
fully delisted companies. In Indian mid-caps those are often the collapses.
A survivorship-free stock-by-stock history wasn't available under a usable
licence.

**What the audit did:**
- Refused to scrape an exchange archive whose terms forbid automated
  collection.
- Ran the check that free, licensed data allows instead: IIMA's published,
  survivorship-adjusted Indian momentum factor.
- Compared it month by month with the rule's own excess return.

**What it found:**
- The Indian momentum premium survives once delisted stocks are counted. The
  idea doesn't depend on missing data.
- But the factor tracked the rule only loosely. It is long-short across the
  whole market, while the rule is long-only in mid-caps, where a missing
  delisted winner hurts. So the size of the rule's own bias stayed
  unmeasured, and the audit said so.
- The lesson: report what the available data can and can't settle. An honest
  "unknown" beats a confident number built on data you can't use.

**What a client gets from this:** knowing which biases their result
carries, how big each one could be, and which are still open.
