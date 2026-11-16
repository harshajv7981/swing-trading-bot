# Momentum across the whole NSE market: plan written before looking at results

Written 2026-11-16, before running anything. Harsha asked to test momentum on
every kind of stock, not only mid-caps: large, small, gold, and sectors
(IT, banks, FMCG, defence). Most of that was already tested:

- **Large, mid and small groups:** only mid-cap M6 passed
  ([momentum-broad-prereg.md](momentum-broad-prereg.md)).
- **13 sector indices** (including IT, Bank and FMCG), and switching between
  size groups: all failed ([index-rotation-prereg.md](index-rotation-prereg.md)).
- **Gold as a 30% mix:** passed ([momentum-gold-prereg.md](momentum-gold-prereg.md)).
- **Defence can't be tested fairly.** The Nifty India Defence index starts in
  Jan 2022, too short for a 2010–19 discovery and holdout split, and the theme
  was picked with hindsight.

The one untested question: does momentum work when it picks from **the whole
market at once**, not from one size group?

## Data and rule

Everything runs on `momentum_broad_study.py`'s code, unchanged; only its
groups differ:
- eod2_data;
- a universe rebuilt each month-end from data known that day: 260+ sessions
  of history and a 60-session median traded value of at least ₹5 crore;
- a 6-1 month score, top 20, equal weight, monthly;
- a suspended stock counted as flat.

| Group | Ranks by traded value | Cost per round trip |
|---|---|---|
| **W1, whole market (judged)** | every stock that qualifies | 0.50% (it mixes large and small) |
| **W2, top 500 (judged)** | 1–500 | 0.40% |
| Mid (reference only) | 101–250, today's rule | 0.40% |

The script also prints M12 (12-1 months) for each group. Those results are
for information only and can't pass.

**Trial count:** W1 and W2 each produce M6 and M12, so **4 momentum trials
are added (N 88 → 92)**, even though only the two M6 arms can pass.

## Pass bar (judged, for W1 M6 and W2 M6)

The same bar as the size study, plus one more check, because a wider universe
is only worth switching to if it beats the rule already running:

1. **Discovery, Jan 2010 – Dec 2019:** mean monthly excess over its own
   equal-weight baseline above 0, with a t-stat above 2.
2. **Holdout, Jan 2020 – Sep 2026:** mean monthly excess above 0, and beating
   the baseline in at least 4 of the 7 years.
3. **Better than today's rule:** a full-period CAGR above the mid-cap M6's in
   the same run.

**If one passes:** it becomes a candidate skill in the learning loop (a
universe setting for `nse-momentum`). It still has to pass the replay, 6 months
in shadow, and Harsha's approval before it can make the adviser list. The
running paper trial doesn't change.

**If neither passes:** mid-cap M6 stays the rule, and the whole-market
question is closed.

## Results (run 2026-11-16)

`scripts/research/momentum_whole_market_study.py`, run on eod2_data as of
its 14 Nov 2026 commit, covering Jan 2010 – Sep 2026.

| Arm | Discovery 2010–19 excess (t) | Holdout 2020–26 excess (t), years ahead | Full CAGR | Worst fall | Turnover | Verdict |
|---|---|---|---|---|---|---|
| **W1 whole market, M6** | +0.32%/month (t 1.1) | +0.38% (t 0.9), 4/7 | +13.4% | -40% | 45%/month | **fail:** discovery t below 2; CAGR below mid M6 |
| **W2 top 500, M6** | +0.36%/month (t 1.2) | +0.46% (t 0.9), 5/7 | +14.4% | -39% | 45%/month | **fail:** discovery t below 2; CAGR below mid M6 |
| Mid M6, reference | +0.37%/month (t 1.7) | +0.95% (t 2.3), 6/7 | +15.4% | -59% | 48%/month | reference only |

For information only, these can't pass: W1 M12 (t 1.5 and 1.9, CAGR +18.1%)
and W2 M12 (t 1.6 and 2.4, CAGR +20.0%). Both miss the discovery bar.

**Verdict: neither whole-market arm passes.** Picking from the whole market
gives shallower worst falls (about -40% against -59%), but a weaker and less
reliable edge over its own baseline than picking among mid-caps. Mid-cap M6
stays the rule, and this question is closed. Nothing goes into the learning
loop.

**One finding about the running rule, which this study didn't judge:** on
today's data, mid-cap M6's discovery t-stat is 1.7, not the 2.1 it showed in
October ([momentum-broad-prereg.md](momentum-broad-prereg.md)). The holdout
is unchanged in substance (t 2.3, 6 of 7 years). eod2_data rewrites its
history (it is force-pushed upstream), and the original pass was close to
the bar. The rule's evidence is weaker than first recorded, which fits the
overfit study's warning (deflated Sharpe 0.78). The forward paper trial and
its October 2027 review stay the real test.
