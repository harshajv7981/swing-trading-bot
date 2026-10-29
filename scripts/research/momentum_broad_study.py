"""Momentum across all NSE stocks by size bucket (docs/research/momentum-broad-prereg.md).

    python scripts/research/momentum_broad_study.py eod2_data/daily

Universe rebuilt each month-end from data known that day: 260+ sessions of
history and a 60-session median traded value of at least Rs 5 crore, ranked by
that value into large (1-100), mid (101-250) and small (251-500).
"""
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot.momentum import (  # noqa: F401  (the paper trial runs the same code)
    NOT_STOCK,
    adjust_jumps,
    load,
)

BUCKETS = {"large": (0, 100, 0.0025), "mid": (100, 250, 0.0040), "small": (250, 500, 0.0060)}
TOP = 20
MIN_VALUE = 5e7  # Rs 5 crore


def main():
    px, val = load(Path(sys.argv[1]))
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    month = px.index.to_period("M")
    me_dates = px.groupby(month).tail(1).index
    me_px = px.ffill(limit=5).loc[me_dates]  # last price within the final week of the month
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]

    out = {b: {"EW": [], "M6": [], "M12": []} for b in BUCKETS}
    prev = {(b, k): pd.Series(dtype=float) for b in BUCKETS for k in ("EW", "M6", "M12")}
    sizes = {b: [] for b in BUCKETS}
    for i, (a, nxt) in enumerate(pairwise(me_dates)):
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        ranked = medval.loc[a][ok].sort_values(ascending=False).index
        r = me_px.loc[nxt] / me_px.loc[a] - 1
        for b, (lo, hi, cost) in BUCKETS.items():
            names = ranked[lo:hi]
            sizes[b].append((nxt, len(names)))
            port = {"EW": names}
            for k, m in (("M6", 6), ("M12", 12)):
                if i < m:
                    port[k] = pd.Index([])
                    continue
                score = (me_px.iloc[i - 1] / me_px.iloc[i - m] - 1).reindex(names).dropna()
                port[k] = score.nlargest(TOP).index
            for k, held in port.items():
                w = pd.Series(1 / len(held), index=held) if len(held) else pd.Series(dtype=float)
                rr = r.reindex(w.index).fillna(0.0)  # no price a month later (suspended): counted flat
                allk = w.index.union(prev[(b, k)].index)
                turn = 0.5 * (w.reindex(allk, fill_value=0) - prev[(b, k)].reindex(allk, fill_value=0)).abs().sum()
                out[b][k].append((nxt, float((w * rr).sum()) - cost * turn, turn, len(held)))
                prev[(b, k)] = w

    def cagr(x):
        return (1 + x).prod() ** (12 / len(x)) - 1

    def maxdd(x):
        eq = (1 + x).cumprod()
        return (eq / eq.cummax() - 1).min()

    for b in BUCKETS:
        res = {k: pd.DataFrame(v, columns=["date", "ret", "turn", "n"]).set_index("date") for k, v in out[b].items()}
        res = {k: v[(v.index >= "2010-01-01") & (v.index < "2026-10-01")] for k, v in res.items()}
        sz = pd.DataFrame(sizes[b], columns=["date", "n"]).set_index("date")["n"]
        by_year = sz.groupby(sz.index.year).mean().round()
        print(f"\n######## {b.upper()} (bucket size by year: "
              + ", ".join(f"{y}:{int(n)}" for y, n in by_year.items() if y >= 2010) + ")")
        base = res["EW"]["ret"]
        print(f"EW: CAGR {cagr(base):+.1%}, max dd {maxdd(base):.1%}; "
              f"2010-19 {cagr(base[base.index.year <= 2019]):+.1%}, 2020-26 {cagr(base[base.index.year >= 2020]):+.1%}")
        for k in ("M6", "M12"):
            x = res[k]["ret"]
            ex = (x - base).dropna()
            print(f"{k}: CAGR {cagr(x):+.1%}, max dd {maxdd(x):.1%}, turnover {res[k]['turn'].mean():.0%}/month")
            for per, e in (("discovery 2010-19", ex[ex.index.year <= 2019]), ("holdout 2020-26", ex[ex.index.year >= 2020])):
                t = e.mean() / e.std() * np.sqrt(len(e))
                print(f"  {per}: excess {e.mean():+.2%}/month (t {t:+.1f}, n {len(e)})")
            yearly = ex.groupby(ex.index.year).apply(lambda s: (1 + s).prod() - 1)
            print("  yearly excess: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))
            print(f"  holdout years beating EW: {(yearly[yearly.index >= 2020] > 0).sum()}/{(yearly.index >= 2020).sum()}")


if __name__ == "__main__":
    main()
