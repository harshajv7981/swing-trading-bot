# Price-rule study: plan written before looking at results

Written 2026-10-28, before any of these rules were run. The news-swarm walk-forward
(Jul–Oct 2026) showed no edge on 194 calls and 2 trades, which is too few to learn
from. This study asks whether simple price-and-volume rules on many years of NSE
data have an edge worth building the bot around. The rules, test split and pass
bar below are fixed here so the results cannot steer them.

## Data

- Daily bars from BennyThadikaran/eod2_data (NSE EQ series, split-adjusted), for
  the current Nifty 100 list (104 files) plus NIFTYBEES for the market.
- Signals from 2010; bars from 2008 so the 200-day averages are ready.
- **Bias:** this is today's Nifty 100, so the winners that joined later and the
  losers that left are missing (survivorship). Every long looks better than it
  was. So each rule is judged against a baseline from the same stocks, not
  against zero alone.

## Ticket (the bot's own, from config/signals.toml)

- The signal comes after the close. Entry is a DAY limit at that close, filled only
  if the next session trades at or below it.
- Stop 2.5 ATR(14), target 2R, time exit at the close of session 5, counting the
  entry day. Hold 10 is reported as a check, not as a second chance.
- Costs 0.25% round trip.
- One position per stock at a time: a signal within 5 sessions of the last one
  taken on that stock is skipped, so overlapping trades don't inflate the counts.

## Rules (long only)

- **A. Volume thrust:** the day closes up 3% or more, on volume at least 2x its
  20-day average, closing in the top 30% of the day's range, with the close above
  the 200-day average. Big up days on heavy volume are usually news days, so this
  is a price-only stand-in for "good news, then drift".
- **B. Pullback in an uptrend:** close above the 200-day average and RSI(2) below
  10 (Connors' short-term mean reversion).
- **C. 52-week high:** close at a new 250-session closing high, on volume at least
  1.5x its 20-day average.
- **D. Shock dip in an uptrend:** the day closes down 4% or more on volume at least
  2x average, with the close still above the 200-day average.

## Baselines (same ticket, same stocks)

- **ALL:** every stock-day.
- **TREND:** every stock-day closing above the 200-day average.

## Split and pass bar

- **Discovery:** 2010–2019. **Holdout:** 2020 to Oct 2026. Nothing about a rule is
  changed after seeing the discovery numbers. A rule either passes as written or
  fails.
- **Discovery pass:** mean R above TREND by at least 0.05R, with a t-stat above 2
  on the difference.
- **Holdout pass (the one that counts):** mean R after costs above 0, above
  TREND, and the rule beats TREND in at least 60% of holdout years.
- With 4 rules, one may pass by luck. A rule that passes only one of the two
  periods counts as a fail.

## Results (run 2026-10-28, after the plan above was committed)

`scripts/research/price_rules_study.py` and `price_rules_tables.py`, on eod2_data
to 23 Oct 2026. R is per trade after 0.25% costs. "vs TREND" is the rule's mean R
minus the TREND baseline's, with a t-stat.

Hold 5 sessions (the plan's main test):

| Rule | Discovery 2010–19 | Holdout 2020–26 | Holdout years beating TREND | Verdict |
|---|---|---|---|---|
| TREND baseline | -0.015R (104,579 days) | -0.003R (92,216) | | |
| A. Volume thrust | -0.037R, -0.022 vs TREND (t -1.4), n 1,565 | +0.015R, +0.018 (t +1.0), n 1,640 | 4/7 | fail |
| B. Pullback in uptrend | +0.010R, +0.025 (t +3.0), n 5,113 | -0.012R, -0.009 (t -1.0), n 4,499 | 2/7 | fail |
| C. 52-week high | -0.044R, -0.030 (t -2.0), n 1,784 | -0.020R, -0.017 (t -1.0), n 1,835 | 3/7 | fail |
| D. Shock dip | -0.076R, -0.061 (t -1.9), n 352 | -0.003R, -0.000 (t 0.0), n 441 | 1/7 | fail |

Hold 10 (the check): TREND is +0.012R and +0.043R. A turns positive in the
holdout (+0.093R, +0.050 vs TREND, t +2.1, 6 of 7 years) but lost in discovery
(-0.059 vs TREND, t -2.9). Under the plan, passing only one period is a fail,
and a rule that flips sign between decades is not one to trade. B is +0.029R
and +0.041R, but within noise of TREND in both periods.

**Verdict: no rule passes.**
- None beats the trend baseline by the planned margin in either period. The
  closest, B at hold 5 in discovery, is +0.025R (t 3.0): half the bar, and it
  reversed in the holdout.
- With this ticket (limit at the close, 2.5 ATR stop, 2R target, 5 sessions), an
  ordinary uptrend day is about break-even after costs. Simple price and volume
  triggers don't add a reliable edge to that, even with survivorship bias
  flattering every long.

## Part 2 plan: patterns in the news calls (written before looking)

Data: the walk-forward's scored judge calls on Harsha's Mac (runs/walkforward-pilot
and runs/walkforward-augoct; score-judge, score-crowd, score-lexicon), at the
72-hour horizon. Discovery: calls from 1 Jul to 31 Aug 2026. Holdout: 1 Sep to
27 Oct 2026.

Slices, fixed now (each is a judge directional call unless noted):
1. Direction: bullish, bearish.
2. News type, by keywords in the headline: results (Q1, Q2, profit, revenue, results,
   earnings), orders (order, contract, wins, bags), ratings (upgrade, downgrade,
   target, buy, sell, rating), regulator (SEBI, RBI, FDA, USFDA, IRDAI, FSSAI,
   penalty, probe, ban), other.
3. Trend: stock above or below its 200-day average on the call day.
4. Market mood on the call day (as the brief recorded it).
5. Conviction: under 0.5, 0.5 and up.
6. Each persona's own directional calls (crowd scorecard rows).

Pass: hit rate of at least 60% on at least 20 calls in discovery, AND at least 55%
on at least 15 calls in the holdout, AND the mean 72-hour return in the call's
direction beats the Nifty's over the same window in both periods. The number of
slices tried is reported next to any pass. With about 25 slices, one or two
will pass discovery by luck, so only holdout survivors count.

## Part 2 results (run 2026-10-28 on the Mac)

18 slices tried (no day had a "bearish" mood), **0 pass**, and none passes even the
discovery stage. Discovery has 149 judge directional calls (Jul–Aug); the
holdout has 45 (Sep–Oct). Excess is the mean 72-hour return in the call's
direction minus the Nifty's, in %.

| Slice | Discovery n / hit / excess | Holdout n / hit / excess |
|---|---|---|
| Bullish | 101 / 47% / +0.01 | 30 / 33% / -0.02 |
| Bearish | 48 / 52% / -0.15 | 15 / 67% / +0.83 |
| Results news | 66 / 45% / +0.11 | 10 / 60% / +1.86 |
| Order news | 9 / 33% / -1.49 | 3 / 33% / -0.38 |
| Rating news | 13 / 69% / +1.52 | 5 / 40% / +1.21 |
| Regulator news | 2 / 0% / -1.08 | 5 / 60% / -0.26 |
| Other news | 59 / 51% / -0.30 | 22 / 36% / -0.38 |
| Above SMA200 | 82 / 45% / -0.08 | 22 / 41% / +0.52 |
| Below SMA200 | 61 / 49% / -0.12 | 23 / 48% / +0.02 |
| Mood bullish | 47 / 55% / -0.00 | 0 |
| Mood defensive | 5 / 20% / -0.23 | 11 / 55% / +0.32 |
| Mood neutral | 97 / 46% / -0.05 | 34 / 41% / +0.26 |
| Conviction < 0.5 | 117 / 49% / +0.03 | 43 / 44% / +0.29 |
| Conviction >= 0.5 | 32 / 47% / -0.30 | 2 / 50% / +0.02 |
| Retail Bull persona | 251 / 47% / +0.07 | 108 / 38% / +0.06 |
| Institutional Bear persona | 128 / 59% / -0.06 | 31 / 61% / +1.08 |
| FII persona | 190 / 46% / -0.06 | 58 / 45% / +0.30 |
| Financial Media persona | 235 / 48% / +0.05 | 65 / 42% / +0.11 |

There are three near-misses, and none of them counts:
- **Institutional Bear persona:** 59% in discovery, but with negative excess, and the Sep–Oct fall flatters a bear.
- **Rating news:** 69% in discovery, but on only 13 calls.
- **Bearish calls:** 67% in the holdout, but only 52% in discovery.

**Overall:** neither the price rules nor the news calls show an edge that survives an honest out-of-sample check.
