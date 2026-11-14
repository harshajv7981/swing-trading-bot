"""The book's untested price rules (docs/research/book-rules-prereg.md).

    python scripts/research/book_rules_study.py data/raw/eod2_data/daily

Five "hold while long" rules from 151 Trading Strategies on the Liquid 250,
judged against equal weight in the same universe. Data, timing, costs, tax and
metrics come from weekly_rules_study.py so the two studies compare.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import weekly_rules_study as w

RULES = ["B1 Single MA", "B2 Two MAs", "B3 Three MAs", "B4 Pivot", "B5 Channel"]
CAP = 0.10


def latch(enter, leave):
    """1 from a day `enter` is true until a day `leave` is true, else 0 (daily state machine)."""
    s = pd.DataFrame(np.nan, index=enter.index, columns=enter.columns)
    s[leave] = 0.0
    s[enter] = 1.0  # enter and leave can't both hold for a channel; enter wins if they ever do
    return s.ffill().fillna(0.0).astype(bool)


def self_check():
    idx = pd.RangeIndex(6)
    e = pd.DataFrame({"a": [0, 1, 0, 0, 0, 1]}, index=idx).astype(bool)
    x = pd.DataFrame({"a": [0, 0, 0, 1, 0, 0]}, index=idx).astype(bool)
    assert latch(e, x)["a"].tolist() == [False, True, True, False, False, True]


def main():
    self_check()
    data = Path(sys.argv[1])
    d = w.load(data)
    O, H, L, C, TV = (d[k] for k in ("O", "H", "L", "C", "TV"))
    del d
    days = C.index
    print(f"{C.shape[1]} stocks, {days[0].date()} to {days[-1].date()}", file=sys.stderr)
    medval = TV.rolling(60, min_periods=40).median().astype("float32")
    keep = medval.columns[(medval >= w.MIN_VALUE).any()]
    O, H, L, C, medval = (f[keep] for f in (O, H, L, C, medval))
    cf = C.ffill(limit=5)
    hist = C.notna().cumsum()
    week = pd.Series(days.to_period("W"), index=days)
    sig_pos = np.flatnonzero((week != week.shift(-1)).to_numpy())

    sma = {n: cf.rolling(n, min_periods=n).mean() for n in (10, 20, 50, 200)}
    state = {
        "B1 Single MA": cf > sma[50],
        "B2 Two MAs": sma[50] > sma[200],
        "B3 Three MAs": (sma[10] > sma[20]) & (sma[20] > sma[50]),
        "B5 Channel": latch(cf > H.rolling(20, min_periods=20).max().shift(1),
                            cf < L.rolling(10, min_periods=10).min().shift(1)),
    }
    # Weekly pivot: the previous week's high, low and close, aligned to each day of this week.
    wk = week.to_numpy()
    wh, wl, wc = H.groupby(wk).max(), L.groupby(wk).min(), cf.groupby(wk).last()
    piv = (wh + wl + wc) / 3
    P, R = piv.shift(1), (2 * piv - wl).shift(1)
    P, R = P.reindex(wk).set_axis(days), R.reindex(wk).set_axis(days)
    state["B4 Pivot"] = (cf > P) & (cf < R)

    names = RULES + ["EW U"]
    rows = {k: [] for k in names}
    held = {k: pd.Series(dtype=float) for k in names}
    for j in range(len(sig_pos) - 1):
        s, x = sig_pos[j], sig_pos[j + 1] + 1
        e = s + 1
        if x >= len(days):
            break
        if days[s] < pd.Timestamp("2009-06-01"):
            continue
        ok = (hist.iloc[s] >= w.MIN_HISTORY) & (medval.iloc[s] >= w.MIN_VALUE) & C.iloc[s].notna() & O.iloc[e].notna()
        u = medval.iloc[s][ok].sort_values(ascending=False).index[:250]
        r = (O.iloc[x].fillna(cf.iloc[x]) / O.iloc[e] - 1).fillna(0.0)
        port = {"EW U": w.equal(u)}
        for k in RULES:
            on = state[k].iloc[s].reindex(u).fillna(False).astype(bool)
            port[k] = w.equal(on[on].index, cap=CAP)
        for k, wt in port.items():
            allk = wt.index.union(held[k].index)
            traded = (wt.reindex(allk, fill_value=0) - held[k].reindex(allk, fill_value=0)).abs().sum()
            rr = r.reindex(wt.index).fillna(0.0)
            gross = float((wt * rr).sum()) + (1 - wt.sum()) * w.CASH_WEEK
            rows[k].append((days[x], gross - w.COST["U"] * traded, 0.5 * traded, len(wt)))
            held[k] = wt * (1 + rr) / (1 + gross)

    res = {k: pd.DataFrame(v, columns=["date", "ret", "turn", "n"]).set_index("date") for k, v in rows.items()}
    res = {k: v[v.index >= w.START] for k, v in res.items()}

    # Monthly mid M6 baseline, as in weekly_rules_study.main.
    me = C.groupby(days.to_period("M")).tail(1).index
    me_px = cf.loc[me]
    m6_rows, mheld = [], pd.Series(dtype=float)
    for i in range(6, len(me) - 1):
        a, nxt = me[i], me[i + 1]
        si = days.get_loc(a)
        ok = (hist.iloc[si] >= w.MIN_HISTORY) & (medval.iloc[si] >= w.MIN_VALUE) & me_px.loc[a].notna()
        nm = medval.iloc[si][ok].sort_values(ascending=False).index[100:250]
        t = w.equal((me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(nm).dropna().nlargest(w.TOP).index)
        rr = (me_px.loc[nxt] / me_px.loc[a] - 1).reindex(t.index).fillna(0.0)
        allk = t.index.union(mheld.index)
        turn = 0.5 * (t.reindex(allk, fill_value=0) - mheld.reindex(allk, fill_value=0)).abs().sum()
        gross = float((t * rr).sum())
        m6_rows.append((nxt, gross - 0.004 * turn))
        mheld = t * (1 + rr) / (1 + gross)
    m6 = pd.Series(dict(m6_rows))
    m6 = m6[m6.index >= w.START]

    nifty = pd.read_csv(data / "nifty 50.csv", usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
    nifty = nifty[~nifty.index.duplicated()].sort_index()
    end = res["EW U"].index[-1]

    def cut(x, lo, hi=end):
        return x[(x.index >= lo) & (x.index <= hi)]

    def nifty_stats(lo):
        n = cut(nifty, lo)
        yrs = (n.index[-1] - n.index[0]).days / 365.25
        g = n.iloc[-1] / n.iloc[0] - 1
        return (1 + g) ** (1 / yrs) - 1, (1 + g * (1 - w.LTCG)) ** (1 / yrs) - 1, w.maxdd(n.resample("W").last().pct_change().dropna())

    H0 = "2020-01-01"
    print(f"\nWeeks {w.START} to {end.date()}: {len(res['EW U'])}")
    print("\n| Portfolio | CAGR | After tax | Worst DD | Sharpe | Turnover/wk | Avg names | 2020+ after tax |")
    print("|---|---|---|---|---|---|---|---|")
    for k in names:
        x = res[k]["ret"]
        print(f"| {k} | {w.cagr(x):+.1%} | {w.after_tax_cagr(x):+.1%} | {w.maxdd(x):.0%} | {w.sharpe(x):.2f} | "
              f"{res[k]['turn'].mean():.0%} | {res[k]['n'].mean():.0f} | {w.after_tax_cagr(cut(x, H0)):+.1%} |")
    print(f"| Monthly mid M6 (trial) | {w.cagr(m6, 12):+.1%} | {w.after_tax_cagr(m6, 12):+.1%} | {w.maxdd(m6):.0%} | "
          f"{w.sharpe(m6, 12):.2f} | | 20 | {w.after_tax_cagr(cut(m6, H0), 12):+.1%} |")
    g, t, dd = nifty_stats(w.START)
    print(f"| Nifty 50 buy & hold | {g:+.1%} | {t:+.1%} | {dd:.0%} | | | | {nifty_stats(H0)[1]:+.1%} |")

    m6_h, nifty_h = w.after_tax_cagr(cut(m6, H0), 12), nifty_stats(H0)[1]
    print("\n| Rule | Discovery 2010–19 | Holdout 2020+ | Holdout years beating EW | Verdict |")
    print("|---|---|---|---|---|")
    for k in RULES:
        ex = (res[k]["ret"] - res["EW U"]["ret"]).dropna()
        dsc, hold = cut(ex, w.START, "2019-12-31"), cut(ex, H0)
        td = dsc.mean() / dsc.std() * np.sqrt(len(dsc))
        th = hold.mean() / hold.std() * np.sqrt(len(hold))
        yearly = ex.groupby(ex.index.year).apply(lambda q: (1 + q).prod() - 1)
        hy = yearly[yearly.index >= w.SPLIT]
        p1, p2 = dsc.mean() > 0 and td > 2, hold.mean() > 0 and (hy > 0).sum() >= 4
        h_tax = w.after_tax_cagr(cut(res[k]["ret"], H0))
        p3 = h_tax > m6_h and h_tax > nifty_h
        v = ("pass, beats M6 and Nifty" if p3 else "pass, but trails M6/Nifty") if p1 and p2 else \
            "fail (discovery)" if p2 else "fail (holdout)" if p1 else "fail"
        print(f"| {k} | {dsc.mean():+.2%}/wk (t {td:+.1f}) | {hold.mean():+.2%}/wk (t {th:+.1f}) | "
              f"{(hy > 0).sum()}/{len(hy)} | {v} |")
        print(f"<!-- {k} yearly excess: " + ", ".join(f"{y}:{q:+.0%}" for y, q in yearly.items()) + " -->")


if __name__ == "__main__":
    main()
