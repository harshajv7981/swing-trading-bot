# Algo trading the mid-cap momentum rule through Dhan

Written 2026-10-31. What the repo can already do, what real automated orders
still need, and the order to do it in. No real order goes in without Harsha
saying so, and the paper trial's rules
([momentum-paper-trial.md](research/momentum-paper-trial.md)) are unchanged.

## What is built

| Piece | State |
|---|---|
| M6 paper trial (`swingbot-momentum`) | Running from 30 Oct 2026 on ₹10L of paper money, booked from eod2_data at each month-end. The 70/30 gold mix is a shadow ledger beside it. |
| Dhan adapter (`swingbot-dhan submit/sync`) | For single swing trades with a broker-held stop (risk tickets). Sandbox only, tested only against a fake Dhan in the tests. |
| **M6 rebalance at Dhan (`swingbot-dhan rebalance`, new)** | Turns the trial's newest picks into Dhan orders: its own sub-book, the trial's equal slots, sells before buys, marketable CNC limits, guards that refuse the whole run. Sandbox `--send`; live is a read-only dry run. Tested only against a fake Dhan. |
| Live orders | Blocked in code (R11): `DhanAPI` refuses every order call in live mode, and `rebalance --send` refuses live too. Unlocking it is a separate change for Harsha to approve. |

A month-end rebalance is about 20 to 40 orders, once a month. That is a good
fit for automation: no intraday speed is needed, and it is far below the rate
where SEBI's retail-algo rules need exchange registration.

## What real automated orders still need

1. **A Dhan account with DDPI**, so delivery sells go through without a TPIN
   prompt each time.
2. **A static IP whitelisted at Dhan.** Since 1 Apr 2026 SEBI's retail-algo
   framework has API orders come only from a registered static IP. Home
   broadband is usually dynamic, so either buy a static IP from the ISP or run
   the bot on a small server in India. Never on GitHub Actions: the repo is
   public and the trial's data is NSE's.
3. **A daily login.** Dhan's access token lasts 24 hours and SEBI requires a
   2FA login each trading day. For a monthly strategy that means one login on
   rebalance day, which also gives Harsha a natural moment to look at the
   orders before they go.
4. **No algo registration** below 10 orders a second (Dhan's API FAQ). The
   rebalance sends about 40 a month, through the adapter's rate limit (R10).
5. **Picks on the day.** The trial books from eod2_data, which lags about a
   week, so its picks arrive days after the month-end. The rebalance refuses
   picks more than 3 sessions old. Real trading needs month-end closes on the
   day, from Dhan's historical data API or NSE's bhavcopy. This is the main
   piece of code still to write.
6. **Quotes.** The rebalance prices orders from Dhan's market quote API, which
   may need Dhan's Data API plan (₹499 a month at the last check). To confirm in
   the sandbox.
7. **Limits for M6 at real money.** `config/risk.toml` was written for swing
   trades: 5 positions, 2 per sector, a stop on every trade. M6 holds 20 stocks
   with no stops, which is how it was tested, so those rules can't apply to it
   as written. The rebalance has its own guards (capital cap, slot size, stale
   picks, halt file), but which hard limits M6 gets with real money (a capital
   cap, a halt on some drawdown) is Harsha's call, and belongs in his own
   locked config. Note the trial itself has no early stop on returns.
8. **Tax.** About 45% turnover a month makes almost every gain short-term
   (20%), against 12.5% long-term on an index fund held a year.

## The path, step by step

| Step | When | What | Gate to the next |
|---|---|---|---|
| 0. Paper trial | Nov 2026 to Oct 2027 | `swingbot-momentum update` books each month on paper. | Runs all year, as pre-registered. |
| 1. Sandbox plumbing | Nov 2026 | Sandbox token from DhanHQ DevPortal; `swingbot-dhan check`, `scrips --refresh`, then `rebalance` and `rebalance --send` on a booked month (raise `max_age_sessions` for the test). | Orders accepted, fills read back, book matches. Fix anything the real sandbox does differently from the fake. |
| 2. Same-day picks | Dec 2026 | Build picks from month-end closes on the day (Dhan data or bhavcopy) and check they match the trial's picks for the same month. | Matches the trial's picks for 2 or 3 months. |
| 3. Shadow on the real account | From Jan 2027 | Live mode, dry run only, on each month-end with real quotes and holdings. Needs the Dhan account, static IP and daily login. | The planned orders match the paper ledger's orders each month, with no hand fixes. |
| 4. Small real money | Only if the Oct 2027 review passes | Harsha approves a code change that unlocks live orders for the rebalance only, with `capital` set small (say ₹1 to 2L). Each month the bot prints the orders and Harsha types `--send` after reading them. | 3 clean months: fills close to the plan, no rejected or stuck orders. |
| 5. Scheduled | After that | The rebalance runs by itself on month-end after Harsha's morning login, with a phone alert of what was sent. Capital raised by hand, in steps. | |

Two things could end this early, and either is fine:

- If the trial can't beat **Nifty Midcap150 Momentum 50** after costs and tax,
  an index fund on that index owns the same idea with no bot, no static IP and
  long-term tax. The October 2027 review compares the two.
- If Dhan's sandbox turns out to differ a lot from its docs, step 1 finds it
  before any money is at stake.

## Sources

- SEBI retail-algo framework, in force 1 Apr 2026 (static IP, daily 2FA, 10
  orders a second threshold): broker summaries such as
  [Fyers](https://support.fyers.in/portal/en/kb/articles/what-are-the-new-sebi-rules-for-retail-algo-trading-from-april-01-2026).
  The SEBI circular itself was not checked here.
- Dhan: [algo ID and registration](https://dhan.co/support/platforms/dhanhq-api/could-you-provide-me-my-algo-id/),
  [token validity](https://dhan.co/support/platforms/dhanhq-api/what-is-the-maximum-validity-of-an-api-access-token-in-dhan-apis/),
  [adding a static IP](https://dhan.co/support/platforms/dhanhq-api/how-to-add-static-ip-on-dhan-for-placing-the-algo-order-s/).
