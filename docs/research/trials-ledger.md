# Every study tried: the trials ledger

One row per pre-registered study, pass or fail, so the number of things tried
is always known. A "pass" after many tries is weaker than one after few: read
any new result against the count here. (Idea from RD-Agent's hypothesis log.)

`tests/test_trials_ledger.py` fails if a `*-prereg.md` in this folder is
missing from the table. Add the row when the prereg is written, and fill in the
outcome when it is judged.

| Written | Study | Outcome |
|---|---|---|
| 2026-10-28 | [Price rules](price-rules-prereg.md) | No rule passes |
| 2026-10-29 | [Multi-month momentum](momentum-prereg.md) | See doc |
| 2026-10-29 | [Momentum beyond the Nifty 100](momentum-broad-prereg.md) | M6 passes |
| 2026-10-29 | [Mid-cap momentum drawdown control](momentum-drawdown-prereg.md) | See doc |
| 2026-10-29 | [Mid-cap momentum plus gold](momentum-gold-prereg.md) | G30 passes |
| 2026-10-29 | [Mid-cap momentum plus low volatility](momentum-lowvol-prereg.md) | Neither fix passes |
| 2026-10-29 | [Smooth mid-cap momentum](momentum-smooth-prereg.md) | Fail |
| 2026-10-30 | [Index rotation](index-rotation-prereg.md) | See doc |
| 2026-10-30 | [Kronos forecasting model](kronos-prereg.md) | Fail |
| 2026-10-30 | [Lead-lag (market seesaw)](lead-lag-prereg.md) | See doc |
| 2026-10-30 | [Machine-learning ranker](ml-model-prereg.md) | Fail (both models) |
| 2026-10-30 | [Weekly stock-pick rules](weekly-rules-prereg.md) | See doc |
| 2026-11-05 | [Is mid-cap M6 overfit?](momentum-overfit-prereg.md) | Real but overstated |
| 2026-11-05 | [TradingAgents, large-cap](trading-agents-prereg.md) | Running; judged after 31 May 2027 |
| 2026-11-07 | [US large-cap momentum trial](us-momentum-prereg.md) | Running |
| 2026-11-07 | [US history study](us-research-prereg.md) | See doc |
| 2026-11-13 | [151 Trading Strategies: untested price rules](book-rules-prereg.md) | No rule passes |
| 2026-11-11 | [Open-to-close (intraday) rules](intraday-open-close-prereg.md) | G-down passes the bar, not trusted; needs minute bars |
| 2026-11-14 | [Smart-money market structure](smc-structure-prereg.md) | No rule passes |
| 2026-11-14 | [M6 and G30 against their passive twins, after tax](passive-twin-prereg.md) | Twin wins both by the rule (M6 -8.0 pts full, +10.7 live; full window rests on back-calculated index) (adds 0 rules) |
| 2026-11-14 | [Rank-buffer M6 (R40)](rank-buffer-prereg.md) | Fail (2010–19 after tax -0.6 pt vs M6, t 1.6); adds 1 momentum trial, N 87 → 88 |
| 2026-11-14 | [Survivorship-free M6](survivorship-free-m6-prereg.md) | Judged test not run (NSE bulk data declined); IIMA cross-check: momentum survives survivorship, weak link to M6 (adds 0 rules) |

Running paper tests with their own rules docs, not counted above:
[mid-cap momentum trial](momentum-paper-trial.md),
[TradingAgents mid-cap](tradingagents-paper-test.md),
[parallel trials](parallel-trials.md).
