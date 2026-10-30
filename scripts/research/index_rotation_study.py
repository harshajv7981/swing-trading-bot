"""Rotation between NSE index groups (docs/research/index-rotation-prereg.md).

    python scripts/research/index_rotation_study.py eod2_data/daily
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from weekly_rules_study import after_tax_cagr, cagr, maxdd, sharpe

SECTORS = ["nifty auto", "nifty bank", "nifty energy", "nifty fmcg", "nifty it", "nifty media", "nifty metal",
           "nifty pharma", "nifty psu bank", "nifty realty", "nifty infrastructure", "nifty commodities",
           "nifty india consumption"]
SIZES = ["nifty 50", "nifty next 50", "nifty midcap 50", "nifty smallcap 50"]
COST = 0.0015
RULES = {"S1 Sector weekly momentum": ("sec", "W", 3), "S2 Sector last-week winners": ("sec", "W", 3),
         "S3 Sector monthly momentum": ("sec", "M", 3), "Z1 Size monthly momentum": ("size", "M", 1)}


def closes(data, names):
    out = {}
    for n in names:
        s = pd.read_csv(data / f"{n}.csv", usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
        out[n] = s[~s.index.duplicated()].sort_index()
    return pd.DataFrame(out).ffill(limit=5)


def run(px, freq, score_fn, top):
    """Net returns of the rule and of EW; signal at a period's last close, trade at the next close."""
    days = px.index
    per = days.to_period(freq)
    sig = np.flatnonzero((per != per[np.r_[1:len(per), len(per) - 1]]) | (np.arange(len(per)) == len(per) - 1))
    sig = sig[:-1]
    rule, ew, prev_r, prev_e = [], [], pd.Series(dtype=float), pd.Series(dtype=float)
    for j in range(len(sig) - 1):
        s, e, x = sig[j], sig[j] + 1, sig[j + 1] + 1
        sc = score_fn(s, j).dropna()
        if len(sc) < len(px.columns) - 1:
            continue
        r = px.iloc[x] / px.iloc[e] - 1
        for out, w, key in ((rule, pd.Series(1 / top, index=sc.nlargest(top).index), "r"),
                            (ew, pd.Series(1 / len(px.columns), index=px.columns), "e")):
            held = prev_r if key == "r" else prev_e
            allk = w.index.union(held.index)
            traded = (w.reindex(allk, fill_value=0) - held.reindex(allk, fill_value=0)).abs().sum()
            g = float((w * r.reindex(w.index)).sum())
            out.append((days[x], g - COST * traded))
            nh = w * (1 + r.reindex(w.index)) / (1 + g)
            if key == "r":
                prev_r = nh
            else:
                prev_e = nh
    return pd.Series(dict(rule)), pd.Series(dict(ew))


def main():
    data = Path(sys.argv[1])
    sets = {"sec": closes(data, SECTORS), "size": closes(data, SIZES)}
    nifty = sets["size"]["nifty 50"]
    res = {}
    for k, (st, freq, top) in RULES.items():
        px = sets[st]
        if freq == "W":
            if k.startswith("S1"):
                fn = lambda s, j, px=px: px.iloc[s - 5] / px.iloc[s - 68] - 1 if s >= 68 else pd.Series(dtype=float)
            else:
                fn = lambda s, j, px=px: px.iloc[s] / px.iloc[s - 5] - 1 if s >= 5 else pd.Series(dtype=float)
        else:
            me = np.flatnonzero(px.index.to_period("M") != px.index.to_period("M")[np.r_[1:len(px), len(px) - 1]])
            def fn(s, j, px=px, me=me):
                prior = me[me < s]
                if len(prior) < 6:
                    return pd.Series(dtype=float)
                return px.iloc[prior[-1]] / px.iloc[prior[-6]] - 1
        res[k] = run(px, freq, fn, top)

    print("| Rule | CAGR | after tax | worst DD | Sharpe | EW CAGR | discovery excess (t) | holdout excess (t), years | holdout after tax | verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for k, (x, ew) in res.items():
        per = 52 if RULES[k][1] == "W" else 12
        ex = (x - ew).dropna()
        d, h = ex[ex.index.year <= 2019], ex[ex.index.year >= 2020]
        td = d.mean() / d.std() * np.sqrt(len(d))
        th = h.mean() / h.std() * np.sqrt(len(h))
        yr = ex.groupby(ex.index.year).apply(lambda q: (1 + q).prod() - 1)
        hy = yr[yr.index >= 2020]
        ok = d.mean() > 0 and td > 2 and h.mean() > 0 and (hy > 0).sum() >= 4
        xh = x[x.index.year >= 2020]
        print(f"| {k} | {cagr(x, per):+.1%} | {after_tax_cagr(x, per):+.1%} | {maxdd(x):.1%} | {sharpe(x, per):.2f} | "
              f"{cagr(ew, per):+.1%} | {d.mean():+.2%} ({td:+.1f}) | {h.mean():+.2%} ({th:+.1f}), {(hy > 0).sum()}/{len(hy)} | "
              f"{after_tax_cagr(xh, per):+.1%} | {'PASS' if ok else 'fail'} |")
        print("  yearly excess: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yr.items()))
    first = min(v[0].index[0] for v in res.values())
    n = nifty[nifty.index >= first]
    yrs = (n.index[-1] - n.index[0]).days / 365.25
    nh = nifty[nifty.index >= "2020-01-01"]
    print(f"\nNifty 50 from {first.date()}: CAGR {(n.iloc[-1] / n.iloc[0]) ** (1 / yrs) - 1:+.1%}; "
          f"holdout {(nh.iloc[-1] / nh.iloc[0]) ** (365.25 / (nh.index[-1] - nh.index[0]).days) - 1:+.1%}")


if __name__ == "__main__":
    main()
