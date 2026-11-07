# Parallel paper trials (phase 3): how the two are run and read

Written 2026-11-07, before either trial booked its first month. Phase 3 of
[parallel-markets-learning-plan.md](../parallel-markets-learning-plan.md).
Nothing here changes either trial. Each keeps its own pre-registration:

| | NSE | US |
|---|---|---|
| Rule | mid-cap 6-1 momentum, top 20 ([momentum-paper-trial.md](momentum-paper-trial.md)) | S&P 500 12-1 momentum, top 20 ([us-momentum-prereg.md](us-momentum-prereg.md)) |
| Money | ₹10 lakh, paper ledger | $100,000, Alpaca paper account |
| Books | NSE month-end close, from eod2_data | NYSE month-end close, orders at the next open |
| Runs | on Harsha's Mac (launchd); NSE data never goes to GitHub | GitHub Actions; state on `us-trial-state` |
| Main benchmark | Mid-cap EW | RSP |
| Judged | Oct 2027 booking | after 12 bookings |

## One record for both

`swingbot-markets trials` reads both trials into the same monthly record:
strategy and version; signal date and its close in UTC (the information
cutoff); universe size and picks; orders intended, filled and rejected; value
in the trial's own currency; the month's return and the return since the start,
after costs; benchmarks since the start; worst fall from a peak; share of the
money invested; and anomalies. Anomalies are reconciliation differences,
rejected or unfilled orders, data notes and stale prices.

## Reading them side by side

- Each trial is judged only against its own pre-registration and its own
  benchmark. Neither trial's result changes the other's rules.
- They are compared only in returns since each start. Rupees are never set
  against dollars, and no currency conversion is made.
- The two trials start a month apart (NSE at the 30 Oct 2026 close, US at
  30 Nov 2026), so "since start" covers different months. The side-by-side
  table lines them up by calendar month.
- An anomaly is reported, never fixed by hand. A month that cannot be booked,
  or a reconciliation difference nobody can explain, stops that trial only, as
  each pre-registration says. A Dhan or eod2_data problem must not stop the US
  trial, and an Alpaca problem must not stop the NSE trial.

## Known at the start

- The NSE trial has not booked its first month: eod2_data's last sync was
  23 Oct 2026, so the 30 Oct close is not in the data yet. The daily job keeps
  checking and will book it when the data arrives, as registered.
- The US trial's first booking is the 30 Nov 2026 close. Its phase 2 fill check
  (1 SPY share bought and sold back) runs on the first weekday market session.
