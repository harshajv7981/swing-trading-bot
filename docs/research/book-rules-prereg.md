# The book's untested price rules: plan written before looking at results

Written 2026-11-13, before running anything. Harsha asked to test the strategies
in Kakushadze & Serur, *151 Trading Strategies* (2018), and keep the best. The
TextToQuant backtest MCP can't do it: the account is on the Free plan, which
covers crypto only (NSE stocks need the paid Power plan) and allows 5 backtests
in total. So the test runs here, on the bot's own data and the harness from
[weekly-rules-prereg.md](weekly-rules-prereg.md).

## Which of the 151 are tested here, and why so few

| Book chapter / strategies | Status |
|---|---|
| Ch 2 options (~54), Ch 5 fixed income, Ch 7 volatility, Ch 9 commodities, Ch 11 structured, Ch 12 convertibles, Ch 13 tax arbitrage, Ch 14 misc, Ch 15 distressed, Ch 16 real estate, Ch 17 cash, Ch 19 global macro, Ch 20 infrastructure | Skipped: not single-equity price/volume swing trades, or need data the bot doesn't have (option chains, yield curves, futures curves, macro series) |
| Ch 4 ETFs, Ch 6 indexes, Ch 8 FX, Ch 18 crypto | Skipped: other asset classes. Index/ETF rotation was already tried in [index-rotation-prereg.md](index-rotation-prereg.md) |
| §3.2–3.8, 3.17–3.21 (earnings momentum, value, low vol, implied vol, multifactor, residual momentum, pairs, M&A arb, KNN, stat-arb optimisation, market making, alpha combos) | Skipped: need fundamentals, options, order books or a pairs/factor model. Low vol tried in [momentum-lowvol-prereg.md](momentum-lowvol-prereg.md), an ML ranker in [ml-model-prereg.md](ml-model-prereg.md) |
| §3.1 price momentum | **Already tested**: the monthly mid-cap M6 (the paper trial) and W2/W4 in weekly-rules |
| §3.9–3.11 mean reversion, §10.3 contrarian | **Already tested** (single-stock stand-ins): W1 reversal, W7 RSI(2), price rules B and D. All failed |
| §3.14 three moving averages | **Partly tested** as the W6 trend template (50 > 150 > 200), which failed. Tested again below with the book's short-window ordering |
| §10.4 trend following | Partly tested via W5 breakout and W8 Supertrend. Tested again below as a channel rule |
| **§3.12 single MA, §3.13 two MAs, §3.14 three MAs, §3.15 support & resistance, §3.16 channel** | **Tested here** |

## Data, universe, timing, costs: unchanged from weekly-rules

Everything is the same as [weekly-rules-prereg.md](weekly-rules-prereg.md):
eod2_data daily bars, the Liquid 250 universe (U) rebuilt each week from data
known that day, signal at the week's last close, trade at the next open, hold to
the open after the next signal day, 0.25% a side, 20.8% STCG each year, same
survivorship caveat. Data to the latest bar in `data/raw/eod2_data/daily`.

## Rules (long only, Liquid 250, read at each signal day)

The book gives these as "hold while the state is long". So each rule holds
**every U stock whose state is long, at equal weight, capped at 10% each, rest
in cash at 6%**. No ranking, no top-N: nothing to tune. Parameters are fixed
here at the common textbook values. No other values will be run.

| Rule | Book § | Long state at the signal day |
|---|---|---|
| **B1 Single MA** | 3.12 | Close above SMA(50) |
| **B2 Two MAs** | 3.13 | SMA(50) above SMA(200) |
| **B3 Three MAs** | 3.14 | SMA(10) > SMA(20) > SMA(50) |
| **B4 Pivot** | 3.15 | Weekly pivot from the previous week's high H, low L, close C: P = (H+L+C)/3, R = 2P − L. Long if this week's close is above P and below R (the book exits at R) |
| **B5 Channel** | 3.16 / 10.4 | Daily state: goes long when the close is above the highest high of the 20 sessions before it, flat when the close is below the lowest low of the 10 sessions before it (Donchian 20/10, the breakout reading of the channel rule) |

## Baselines

- **EW U:** equal weight in all of U, reset weekly (the rules are judged against this).
- **Monthly mid M6:** the paper trial's rule, recomputed as in weekly-rules.
- **Nifty 50** buy and hold.

## Pass bar (same as weekly-rules)

- **Discovery** (weeks ending 2010–2019): mean weekly excess over EW U, after
  costs, above 0 with t > 2.
- **Holdout** (2020 to the end of the data): mean weekly excess above 0, and
  beating EW U in at least 4 of the holdout calendar years.
- **Worth switching to:** passes both, AND its holdout after-tax CAGR beats the
  monthly mid M6 and the Nifty 50.
- **Multiple testing:** 13 price rules already failed (price rules A–D, weekly
  W1–W9), and this is the 17th study in [trials-ledger.md](trials-ledger.md).
  Adding 5 makes 18 price rules. A single pass at t ≈ 2 is about what luck alone
  gives across 18 tries. So any pass is reported as "candidate for a forward
  paper trial", never as proven, and nothing is wired into the bot from this doc.

## Results (run 2026-11-13, after the plan above was written)

`scripts/research/book_rules_study.py data/raw/eod2_data/daily`, 880 weeks to
9 Nov 2026. Net of 0.25% a side; "after tax" is after 20.8% STCG each year
(Nifty: 13% LTCG at the end). Check on the code: EW U (+9.2%) and the monthly M6
(+14.8%) match weekly-rules (+9.0%, +14.1%) given 3 more weeks of data.

| Portfolio | CAGR | After tax | Worst DD | Sharpe | Turnover/wk | Avg names | 2020+ after tax |
|---|---|---|---|---|---|---|---|
| B1 Single MA | +2.9% | +2.2% | -58% | -0.05 | 26% | 113 | +8.8% |
| B2 Two MAs | +10.4% | +8.4% | -45% | 0.30 | 7% | 128 | +13.8% |
| B3 Three MAs | +0.4% | +0.3% | -66% | -0.18 | 35% | 77 | +6.8% |
| B4 Pivot | -9.5% | -9.5% | -84% | -0.67 | 78% | 59 | -5.3% |
| B5 Channel | +1.2% | +0.8% | -57% | -0.14 | 28% | 84 | +6.1% |
| EW U | +9.2% | +7.4% | -53% | 0.25 | 3% | 207 | +14.5% |
| **Monthly mid M6 (paper trial)** | **+14.8%** | **+12.0%** | -59% | **0.44** | | 20 | **+24.9%** |
| Nifty 50 buy & hold | +10.7% | +9.9% | -35% | | | | +10.0% |

| Rule | Discovery 2010–19 (excess vs EW U) | Holdout 2020+ | Holdout years beating EW | Verdict |
|---|---|---|---|---|
| B1 Single MA | -0.12%/wk (t -2.8) | -0.12%/wk (t -2.6) | 0/7 | fail |
| B2 Two MAs | +0.04%/wk (t +1.1) | -0.02%/wk (t -0.4) | 4/7 | fail |
| B3 Three MAs | -0.17%/wk (t -2.9) | -0.17%/wk (t -2.8) | 1/7 | fail |
| B4 Pivot | -0.33%/wk (t -8.2) | -0.41%/wk (t -6.5) | 0/7 | fail |
| B5 Channel | -0.14%/wk (t -3.1) | -0.18%/wk (t -3.1) | 0/7 | fail |

**Verdict: no rule passes.** Four of the five are reliably *worse* than holding
the whole universe, in both periods (t below −2.5). B2 (50/200 golden cross) is
the only one near equal weight: a slightly smaller drawdown (−45% vs −53%) for
about the same return, no edge. The rules lose in two ways: they sit out the
rebounds after a sell-off, and they churn 26–78% of the book a week at 0.5% a
round trip. That's the same pattern weekly-rules found for its fast rules.

## All of the book's testable swing rules, ranked (this study plus earlier ones)

Ranked by after-tax CAGR, with Sharpe and worst drawdown beside it. Every row
except the paper trial **failed its pre-registered bar**. The ranks only show
how badly each failed. 18 price rules tried in all.

| # | Rule (book §) | Study | After tax | Sharpe | Worst DD | Verdict |
|---|---|---|---|---|---|---|
| — | **Monthly mid M6, the paper trial (§3.1)** | momentum-broad | **+12.0%** | **0.44** | -59% | passed; real but overstated ([momentum-overfit](momentum-overfit-prereg.md)) |
| 1 | B2 Two MAs 50/200 (§3.13) | this | +8.4% | 0.30 | -45% | fail |
| 2 | W4 M6 weekly (§3.1) | weekly-rules | +8.3% | 0.28 | -69% | fail |
| 3 | W6 Trend template (§3.14) | weekly-rules | +8.2% | 0.28 | -52% | fail |
| 4 | W2 Short momentum (§3.1) | weekly-rules | +7.3% | 0.24 | -64% | fail |
| 5 | W5 Breakout (§10.4) | weekly-rules | +3.9% | 0.01 | -40% | fail |
| 6 | B1 Single MA 50 (§3.12) | this | +2.2% | -0.05 | -58% | fail |
| 7 | B5 Channel 20/10 (§3.16/10.4) | this | +0.8% | -0.14 | -57% | fail |
| 8 | B3 Three MAs 10/20/50 (§3.14) | this | +0.3% | -0.18 | -66% | fail |
| 9 | W3 52-week high | weekly-rules | -0.1% | -0.19 | -48% | fail |
| 10 | W7 RSI(2) pullback (§3.9 stand-in) | weekly-rules | -0.9% | -0.29 | -51% | fail |
| 11 | B4 Pivot (§3.15) | this | -9.5% | -0.67 | -84% | fail |
| 12 | W1 Reversal (§10.3 contrarian) | weekly-rules | -10.3% | -0.47 | -90% | fail |

(Weekly-rules' numbers run to 19 Oct 2026, this study's to 9 Nov. Price rules
A–D in [price-rules-prereg.md](price-rules-prereg.md) are per-trade R, not CAGR,
so they aren't in this table. All four failed.)

**Pick:** none of the book's single-stock price rules beats the monthly mid-cap
momentum rule the bot is already paper trading, and none beats plain equal
weight by a margin that survives the holdout. The paper trial stays the
baseline. Nothing here is a candidate for a forward paper trial.

Caveats: survivorship (today's listed stocks only) flatters every long rule,
these rules included. One fixed parameter set per rule, so no tuning to overfit.
But 18 tries means a lone pass would have been weak evidence anyway. Past
backtests don't predict future returns. Research only, not investment advice.
