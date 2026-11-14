# Mid-cap momentum paper trial: rules fixed before it starts

Written 2026-10-29, before the first booking. Mid-cap M6 is the only rule that
passed both periods in [momentum-broad-prereg.md](momentum-broad-prereg.md).
This runs it forward on paper, with no model calls, to see whether it behaves
the same on months nobody has looked at yet. Nothing here places an order.

## The rule (as tested, nothing tuned)

- **When:** the last NSE session of each month, at that day's closes. The first
  booking is 30 Oct 2026, so November 2026 is the first month held.
- **Universe:** every stock in eod2_data with 260+ sessions of history and a
  60-session median traded value of at least ₹5 crore, ranked by that value.
  Ranks 101–250 are the mid-cap bucket. EQ series only for the ranking.
- **Picks:** the 20 stocks in the bucket with the best return from 6 month-ends
  back to 1 month-end back. They are held in equal weight until the next month-end.
- **Paper account:** ₹10 lakh, whole shares, 0.20% a side on the value traded
  (the study's 0.40% round trip). Dividends are left out, as in the study.
- **Code:** `swingbot-momentum` (`src/swingbot/momentum.py`). The ranking is the
  study's own code: `scripts/research/momentum_broad_study.py` now imports it
  from there.

## What it is compared with

All three are price-only, like the paper account:
- **Mid-cap EW:** equal weight in the whole bucket, the study's baseline.
- **Nifty Midcap 150:** the market the bucket stands in for.
- **Nifty Midcap150 Momentum 50:** NSE's own mid-cap momentum index. Index
  funds track it. If the trial can't beat it, buying such a fund is the simpler
  way to own the same idea.

Added 2026-10-29, after [momentum-gold-prereg.md](momentum-gold-prereg.md) passed:
- **Gold ETF (GOLDBEES):** gold on its own.
- **70/30 with gold:** a shadow ledger. It is the paper portfolio's month-by-month
  return on 70% of the money, plus GOLDBEES on the other 30%. The mix is set
  back to 70/30 at each December booking, paying 0.20% on the equity moved and
  0.10% on the gold moved. It is computed from the same ledger, so the paper
  trades do not change. The October 2027 review judges both the trial and this
  mix.

## Replay check (done before the start, not evidence)

`swingbot-momentum` was run over Sep 2025 to Sep 2026, data that the study's
holdout already covered:
- It chose the same 20 stocks as the study in all 13 months.
- Each month's return came within 0.3 points of the study's, except April 2026.
  That month two holdings moved to the BE (trade-for-trade) series. The study
  counted them as flat; the paper account sells them at the BE price, as a real
  holder could.
- 12-month result: +25.3% for the paper account, against +6.0% for mid-cap EW,
  +3.9% for the Nifty Midcap 150 and +3.5% for the Momentum 50 index. This
  checks the code, not the edge, because these months were already in the
  holdout.

## How it is judged

- **Every month:** the report gives the value, the month's return, the return
  since the start against all three comparisons, turnover, costs, holdings,
  orders and the worst fall from a peak.
- **No early stop on returns.** Paper costs nothing, and the backtest had
  losing years (mid-cap M6 trailed EW in 1 of 7 holdout years). The trial stops
  early only if the process breaks: a month that can't be booked, or prices
  that are wrong.
- **At 12 months** (the booking at the end of Oct 2027), real money is
  discussed only if all of these hold:
  1. Every month was booked from the data with no hand fixes.
  2. The paper account beat mid-cap EW over the 12 months, after costs.
  3. Harsha has a written plan for a fall like the backtest's worst (about
     -60%; ₹10 lakh would have dropped to about ₹4 lakh).
  4. The money side is checked against Indian tax. With about 45% turnover a
     month, almost every gain is short-term (taxed at 20%), while an index fund
     held over a year is taxed at 12.5% on gains.

  An overfit check ([momentum-overfit-prereg.md](momentum-overfit-prereg.md),
  2026-11-05) found the edge broad but its size overstated: all 72 neighbouring
  rules beat EW, but the deflated Sharpe ratio was 0.78, short of 0.95. A year
  behind EW would not by itself mean the rule broke.

  Twelve months cannot prove an edge: the backtest needed years to reach a
  t-stat of 2. The year is a check that the rule still behaves as tested, not
  proof that it works.

## Real-money limits (Harsha's written plan, 2026-11-14)

This is the plan review gate 3 asks for. It was written after
[passive-twin-prereg.md](passive-twin-prereg.md) sent any real money to the
passive fund rather than M6.

- **When:** no real money goes in before the October 2027 review. The paper
  trial and the G30 shadow ledger run as booked until then.
- **Cap:** ₹2.5 lakh at most, ever, across the whole position. It is never
  money needed to live on if the job goes.
- **Review point:** at a fall of 25% from the position's peak, stop adding
  money and review. 25% is about what the Momentum 50 fund lost from Sep 2024
  to Mar 2025.
- **A fall of about 60%:** hold what is invested and add nothing new until
  the value recovers above the -25% review point. No selling in the fall.
- **Changing these:** only in writing, dated, before the October 2027
  review. Never during a fall.

**Tracked by `swingbot-real`.** The limits live in `config/real_money.toml`,
and holdings in the git-ignored `data/state/real_position.json`.
- **Recording trades:** record each real buy or sell after making it
  (`swingbot-real buy|sell`). Buys before the start date, over the cap, or
  while the position is past the review drawdown are refused.
- **Reporting:** `swingbot-real report` shows the value, the time-weighted
  return, the drawdown, the cap used, this tax year's booked gains and the
  exemption room left, with the M6 paper trial alongside.
- **The nightly check:** the nightly job runs `swingbot-real check`. It
  notifies once at the review drawdown, and once again on recovery. It also
  notifies each December when there are two or more targets (rebalance), and
  each March when long-term gain fits in the ₹1.25 lakh exemption.

What the money would go into is decided at the review. By build 2's rule
it is the Momentum 50 fund, optionally 70/30 with a gold ETF, unless the
trial and a new pre-registered comparison say otherwise.

## Known gaps against real trading

- **Fills at the month-end close.** Real orders would go in at the close or the
  next morning, at slightly different prices. Since 2026-11-14 each booked
  month also records what filling at the next session would have cost
  against the close, weighted by order value. It uses the next open and the
  next (high + low + close) / 3 as a VWAP stand-in (`shortfall` in the ledger,
  "Fills at the next session" in the report). October 2026: next open -0.26%,
  typical +0.30%. **Rule, fixed before the November booking:** after the
  January 2027 booking (three measured rebalances), if the average of the two
  measures across those months is above +0.25% per rupee traded, the
  backtest's 0.20% a side was too low. M6 is then re-run at 0.20% plus the
  observed figure before any real-money discussion. This is information for
  the October 2027 review, not a stop rule for the trial.
- **Data lag.** eod2_data syncs about once a week, so a month is booked a few
  days late, still from that day's closes and nothing later. Real trading
  would need same-day data (Dhan's API or NSE's daily bhavcopy).
- **Delisted stocks.** A held stock that stops trading is valued at its last
  close and kept until it trades again. Its gain or loss in the meantime is
  unknown.
- **Data stays private.** eod2_data comes from NSE. The ledger and reports stay
  in git-ignored folders on the Mac. They never go through GitHub Actions or
  into the repo.
