# Survivorship-free M6: plan written before looking at results

Written 2026-11-14, before downloading or running anything. Build 3 of
[roadmap-2026-11.md](roadmap-2026-11.md).

Every M6 result so far comes from eod2_data, which lacks fully delisted stocks
such as DHFL, Jet Airways and Satyam
([momentum-broad-prereg.md](momentum-broad-prereg.md)). That bias is largest in
mid-caps, where M6 lives. In the large-cap bucket, survivorship accounted for
most of an apparent +27% a year. This study re-runs **M6 unchanged** on a
panel that includes every stock that traded. It re-tests an existing rule, so
it **adds no rules** (N stays 87).

## Data

- **Panel:** every NSE capital-market bhavcopy from 1 Jan 2008 to 30 Sep 2026,
  from nsearchives.nseindia.com:
  - Old format (`cm<DD><MON><YYYY>bhav.csv.zip`) up to the switch in July 2024.
  - The UDiFF format already read by `swingbot.bhavcopy` after that.
  - Rows kept: EQ and BE series, close and traded value, every symbol that
    appears on any day.
  - A day with no file (404) is a holiday.
- **Coverage check, run before any result is computed:**
  - The number of symbols in the panel against eod2_data.
  - DHFL, JETAIRWAYS and SATYAMCOMP must be present until their last trading
    day.
  - If any of the three is missing, the panel is fixed before going on.
- **Symbol changes:**
  - Where the file has an ISIN, rows are joined by ISIN, so a renamed stock is
    one series.
  - Before ISINs appear, a rename starts a new series. That breaks a stock's
    6-month history at a rename; the number of such breaks is reported.
- **Prices:** the bhavcopy is unadjusted. Closes go through the same
  `adjust_jumps` rule that `swingbot.momentum.load` applies to eod2_data, so
  splits and bonuses are handled the same way in both.

## Rule (unchanged)

`m6_and_ew` from `momentum_gold_study.py`:

- **Universe:** the mid bucket, ranks 101–250 by 60-day median traded value,
  at least ₹5 crore, at least 260 days of history.
- **Selection and costs:** top 20 by 6-1 month return, monthly, 0.40% per
  unit of turnover.
- **Baseline:** equal weight across the same mid bucket.
- **A stock with no price at the next month-end** counts as flat for that
  month, as in every earlier study. A stock that never trades again is held
  at its last close.
- **For information only:** the same run with such a stock counted at -100%
  in the month it stops trading. This is the worst case, and the number of
  such events is reported.

## Periods

- **Discovery:** Jan 2010 – Dec 2019.
- **Holdout:** Jan 2020 – Sep 2026.

## Pass bar (judged)

M6 keeps its standing only if both hold:

1. Its average monthly excess over the mid EW, net of costs, is at least
   **+0.3%** over the full period. The overfit study already expects the true
   figure to be near +0.3%.
2. The t-stat of that excess is at least **1.5 in each period**.

**If it fails:**
- The real-money plan for M6 is dropped, and G30, whose equity sleeve is M6,
  is dropped with it.
- The paper trial carries on to October 2027 as a test of the process, not as
  a candidate for money.

**If it passes:** nothing is approved. The October 2027 gates in
[momentum-paper-trial.md](momentum-paper-trial.md) and the passive-twin rule
in [passive-twin-prereg.md](passive-twin-prereg.md) still decide.

## Cross-check (information, not judged)

- IIMA's survivorship-adjusted four-factor library: the monthly momentum
  factor (WML), Oct 1993 – Dec 2025
  ([IIMA](https://faculty.iima.ac.in/~iffm/Indian-Fama-French-Momentum/)).
- Reported:
  - the correlation of M6's monthly excess with WML over 2010–2025;
  - WML's mean over the same months.
- This is a different universe, so it is a sense check only.

## Results

**Judged test: not run.** The panel needs about 4,200 bhavcopies from
nsearchives. NSE's terms of use forbid automated collection (see
`src/swingbot/bhavcopy.py`), so on 2026-11-14 Harsha chose not to do the
backfill. M6's survivorship bias stays unmeasured, and the pass bar above
stays open. It can be run only if a properly licensed history of delisted
stocks turns up.

Licensed routes found on 2026-11-14 (neither has a public price):
- **NSE Data & Analytics (NDAL)**, through NSE's
  [end-of-day historical data subscription](https://www.nseindia.com/static/market-data/eod-historical-data-subscription),
  requested by email to marketdata@nse.co.in. Before paying, get it in writing
  that the history includes securities later delisted.
- **Exchange Data International's NSE end-of-day feed.** A third-party post
  says it includes delisted stocks; this is [unverified].

If either is bought, the data section above gets a dated amendment naming the
source, before any M6 result is computed.

**Cross-check (information, not judged), run 2026-11-14:**
`scripts/research/iima_wml_check.py`, using IIMA's survivorship-bias-adjusted
monthly file, release 2025-12.

| Months | M6 excess over mid EW | IIMA WML | Correlation |
|---|---|---|---|
| 2010–25 (192) | +0.56%/month (t +2.8) | +1.23%/month (t +3.6) | +0.27 |
| 2010–19 (120) | +0.38%/month (t +1.8) | +1.51%/month (t +3.3) | +0.15 |
| 2020–25 (72) | +0.86%/month (t +2.0) | +0.77%/month (t +1.5) | +0.46 |

What it shows and what it doesn't:
- **The Indian momentum premium survives survivorship adjustment.** WML
  stays positive with t above 3 over 2010–25 once delisted stocks are
  included. So the idea behind M6 doesn't depend on missing data.
- **It can't size M6's bias.** WML is long winners and short losers across
  the market. Delisted stocks mostly fall into its losers leg, where a
  collapse helps WML. M6 is long-only in mid-caps, where a delisted winner
  would hurt it. The correlation of 0.27 overall and 0.15 in 2010–19 says WML
  is a weak stand-in for M6.
- **WML weakened after 2020** (t +1.5), while M6's excess grew. That fits a
  mid-cap boom more than a lasting edge. It is a reading, not a test.

The planning assumption is unchanged: M6's real excess is likely nearer
+0.3%/month than +0.6% ([momentum-overfit-prereg.md](momentum-overfit-prereg.md)),
with a survivorship cut of unknown size on top.
