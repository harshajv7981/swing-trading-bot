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

  Twelve months cannot prove an edge: the backtest needed years to reach a
  t-stat of 2. The year is a check that the rule still behaves as tested, not
  proof that it works.

## Known gaps against real trading

- **Fills at the month-end close.** Real orders would go in at the close or the
  next morning, at slightly different prices.
- **Data lag.** eod2_data syncs about once a week, so a month is booked a few
  days late, still from that day's closes and nothing later. Real trading
  would need same-day data (Dhan's API or NSE's daily bhavcopy).
- **Delisted stocks.** A held stock that stops trading is valued at its last
  close and kept until it trades again. Its gain or loss in the meantime is
  unknown.
- **Data stays private.** eod2_data comes from NSE. The ledger and reports stay
  in git-ignored folders on the Mac. They never go through GitHub Actions or
  into the repo.
