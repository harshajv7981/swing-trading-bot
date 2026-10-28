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
