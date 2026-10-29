"""Smooth mid-cap momentum, frog in the pan (docs/research/momentum-smooth-prereg.md).

    python scripts/research/momentum_smooth_study.py eod2_data/daily

Mid bucket (ranks 101-250 by traded value, rebuilt monthly), monthly, drifting
weights. A: top 20 by 6-1 month return. S: of the top 40, the 20 with the
lowest information discreteness (ID) over the same window. S + 30% gold is
information only.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot.momentum import adjust_jumps, load

LO, HI = 100, 250
TOP, WIDE = 20, 40
COST = 0.0040  # per unit of one-way turnover, as in momentum_broad_study.py
GOLD_COST = 0.0010
GOLD_SHARE = 0.30
MIN_VALUE = 5e7  # Rs 5 crore
MIN_DAYS = 80
CASH_MONTH = 1.06 ** (1 / 12) - 1
GOLD_FILE = "goldbees.csv"


def cagr(x):
    return (1 + x).prod() ** (12 / len(x)) - 1


def maxdd(x):
    eq = (1 + x).cumprod()
    return (eq / eq.cummax() - 1).min()


def sharpe(x):
    ex = x - CASH_MONTH
    return ex.mean() / ex.std() * np.sqrt(12)


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x))


def equal(names):
    return pd.Series(1 / len(names), index=names) if len(names) else pd.Series(dtype=float)


def with_gold(x, gold):
    """x held as 70% next to 30% gold, back to target at each December month-end."""
    w = np.array([1 - GOLD_SHARE, GOLD_SHARE])
    out = []
    for d in x.index:
        r = np.array([x[d], gold[d]])
        gross = float(w @ r)
        w = w * (1 + r) / (1 + gross)
        cost = 0.0
        if d.month == 12:
            moved = abs(w[1] - GOLD_SHARE)
            cost = moved * (COST + GOLD_COST)
            w = np.array([1 - GOLD_SHARE, GOLD_SHARE])
        out.append(gross - cost)
    return pd.Series(out, index=x.index)


def main():
    data = Path(sys.argv[1])
    px, val = load(data)
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]
    dr = px.pct_change(fill_method=None)
    ups, downs, days = ((dr > 0).cumsum().loc[me_dates], (dr < 0).cumsum().loc[me_dates],
                        dr.notna().cumsum().loc[me_dates])

    ew_prev = pd.Series(dtype=float)
    held = {"A": pd.Series(dtype=float), "S": pd.Series(dtype=float)}
    rows = {"A": [], "S": []}
    ew_rows = []
    for i in range(len(me_dates) - 1):
        a, nxt = me_dates[i], me_dates[i + 1]
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        names = medval.loc[a][ok].sort_values(ascending=False).index[LO:HI]
        # No price a month later (suspended): counted flat, as before.
        r = (me_px.loc[nxt] / me_px.loc[a] - 1).fillna(0.0)

        ew = equal(names)
        allk = ew.index.union(ew_prev.index)
        turn = 0.5 * (ew.reindex(allk, fill_value=0) - ew_prev.reindex(allk, fill_value=0)).abs().sum()
        ew_rows.append((nxt, float((ew * r.reindex(ew.index)).sum()) - COST * turn))
        ew_prev = ew

        if i < 6:
            continue
        score = (me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(names).dropna()
        n = (days.iloc[i - 1] - days.iloc[i - 6]).reindex(names)
        up = (ups.iloc[i - 1] - ups.iloc[i - 6]).reindex(names) / n
        down = (downs.iloc[i - 1] - downs.iloc[i - 6]).reindex(names) / n
        info_disc = (np.sign(score) * (down - up))[n >= MIN_DAYS].dropna()
        wide = score.nlargest(WIDE).index
        targets = {"A": equal(score.nlargest(TOP).index),
                   "S": equal(info_disc.reindex(wide).dropna().nsmallest(TOP).index)}
        for k, target in targets.items():
            cur = held[k]
            allk = target.index.union(cur.index)
            turn = 0.5 * (target.reindex(allk, fill_value=0) - cur.reindex(allk, fill_value=0)).abs().sum()
            rr = r.reindex(target.index).fillna(0.0)
            gross = float((target * rr).sum())
            rows[k].append((nxt, gross - COST * turn, turn))
            held[k] = target * (1 + rr) / (1 + gross)

    def window(df):
        return df[(df.index >= "2010-01-01") & (df.index < "2026-10-01")]

    ew_s = window(pd.DataFrame(ew_rows, columns=["date", "ret"]).set_index("date"))["ret"]
    res = {k: window(pd.DataFrame(v, columns=["date", "ret", "turn"]).set_index("date")) for k, v in rows.items()}

    g = pd.read_csv(data / GOLD_FILE, usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
    g = adjust_jumps(g[~g.index.duplicated()].sort_index().dropna().astype(float))
    g_me = g.reindex(g.index.union(me_dates)).ffill(limit=5).loc[me_dates]
    gold = (g_me / g_me.shift() - 1).reindex(ew_s.index).fillna(0.0)

    def disc(s):
        return s[s.index.year <= 2019]

    def hold(s):
        return s[s.index.year >= 2020]

    series = {"Mid EW": ew_s, "A": res["A"]["ret"], "S": res["S"]["ret"],
              "A + 30% gold": with_gold(res["A"]["ret"], gold), "S + 30% gold (info)": with_gold(res["S"]["ret"], gold)}
    for k, x in series.items():
        extra = f", turnover {res[k]['turn'].mean():.0%}/month" if k in res else ""
        print(f"{k}: CAGR {cagr(x):+.1%}, max dd {maxdd(x):.1%}, Sharpe {sharpe(x):.2f}{extra}; "
              f"2010-19 {cagr(disc(x)):+.1%} / Sharpe {sharpe(disc(x)):.2f}; "
              f"2020-26 {cagr(hold(x)):+.1%} / Sharpe {sharpe(hold(x)):.2f}")

    s, a = res["S"]["ret"], res["A"]["ret"]
    ex, vs_a = s - ew_s, s - a
    yearly_ex = ex.groupby(ex.index.year).apply(lambda v: (1 + v).prod() - 1)
    hold_years = yearly_ex[yearly_ex.index >= 2020]
    print(f"\nS over EW: discovery {disc(ex).mean():+.2%}/month (t {tstat(disc(ex)):+.1f}), "
          f"holdout {hold(ex).mean():+.2%}/month (t {tstat(hold(ex)):+.1f}), "
          f"holdout years beating EW {(hold_years > 0).sum()}/{len(hold_years)}")
    print(f"S minus A: discovery {disc(vs_a).mean():+.2%}/month (t {tstat(disc(vs_a)):+.1f}), "
          f"holdout {hold(vs_a).mean():+.2%}/month (t {tstat(hold(vs_a)):+.1f})")
    by_year = (1 + s).groupby(s.index.year).prod() - (1 + a).groupby(a.index.year).prod()
    print("S minus A by year: " + ", ".join(f"{y}:{v:+.0%}" for y, v in by_year.items()))
    c1 = (disc(ex).mean() > 0 and tstat(disc(ex)) > 2 and hold(ex).mean() > 0 and (hold_years > 0).sum() >= 4)
    c2 = disc(vs_a).mean() > 0 and hold(vs_a).mean() > 0
    print(f"VERDICT S: beats EW {'pass' if c1 else 'fail'}, beats A in both periods {'pass' if c2 else 'fail'} "
          f"-> {'PASS' if c1 and c2 else 'FAIL'}")


if __name__ == "__main__":
    main()
