"""Last-5-years view of the whole-market study (docs/research/momentum-whole-market-prereg.md): information only.

    python scripts/research/momentum_whole_market_last5.py data/raw/eod2_data/daily

The window was chosen after the full run, so nothing here can pass a bar or count as a trial.
"""
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot.momentum import load
from swingbot.research import after_tax_cagr, hold_after_tax_cagr

GROUPS = {"Mid (running rule)": (100, 250, 0.0040), "W1 whole market": (0, None, 0.0050), "W2 top 500": (0, 500, 0.0040)}
FROM, TO = "2021-11-01", "2026-11-01"  # 60 month-ends: Nov 2021 - Oct 2026

px, val = load(Path(sys.argv[1]))
me = px.groupby(px.index.to_period("M")).tail(1).index
me_px = px.ffill(limit=5).loc[me]
hist = px.notna().cumsum().loc[me]
medval = val.rolling(60, min_periods=40).median().loc[me]
nifty = pd.read_csv(Path(sys.argv[1]) / "nifty 50.csv", usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
nifty = nifty[~nifty.index.duplicated()].sort_index().reindex(me, method="ffill").pct_change()

out = {(g, k): [] for g in GROUPS for k in ("EW", "M6", "M12")}
prev = {key: pd.Series(dtype=float) for key in out}
for i, (a, nxt) in enumerate(pairwise(me)):
    ok = (hist.loc[a] >= 260) & (medval.loc[a] >= 5e7) & me_px.loc[a].notna()
    ranked = medval.loc[a][ok].sort_values(ascending=False).index
    r = me_px.loc[nxt] / me_px.loc[a] - 1
    for g, (lo, hi, cost) in GROUPS.items():
        names = ranked[lo:hi]
        port = {"EW": names}
        for k, m in (("M6", 6), ("M12", 12)):
            port[k] = (me_px.iloc[i - 1] / me_px.iloc[i - m] - 1).reindex(names).dropna().nlargest(20).index if i >= m else pd.Index([])
        for k, held in port.items():
            w = pd.Series(1 / len(held), index=held) if len(held) else pd.Series(dtype=float)
            allk = w.index.union(prev[(g, k)].index)
            turn = 0.5 * (w.reindex(allk, fill_value=0) - prev[(g, k)].reindex(allk, fill_value=0)).abs().sum()
            out[(g, k)].append((nxt, float((w * r.reindex(w.index).fillna(0.0)).sum()) - cost * turn))
            prev[(g, k)] = w


def cut(x):
    return x[(x.index >= FROM) & (x.index < TO)]


ser = {key: cut(pd.Series(dict(v))) for key, v in out.items()}
n50 = cut(nifty)
print(f"Window {ser[('W1 whole market', 'M6')].index[0].date()} to {ser[('W1 whole market', 'M6')].index[-1].date()}, "
      f"{len(n50)} months. Nifty 50: CAGR {(1 + n50).prod() ** (12 / len(n50)) - 1:+.1%}, held after 13% LTCG "
      f"{hold_after_tax_cagr(n50):+.1%}")
print("| Group | Rule | CAGR | After 20.8% tax | Excess over own EW (t) | Years ahead | Worst fall |")
print("|---|---|---|---|---|---|---|")
for g in GROUPS:
    ew = ser[(g, "EW")]
    for k in ("EW", "M6", "M12"):
        x = ser[(g, k)]
        eq = (1 + x).cumprod()
        ex = (x - ew).dropna()
        yearly = ex.groupby(ex.index.year).apply(lambda s: (1 + s).prod() - 1)
        t = ex.mean() / ex.std() * np.sqrt(len(ex)) if k != "EW" else float("nan")
        print(f"| {g} | {k} | {eq.iloc[-1] ** (12 / len(x)) - 1:+.1%} | {after_tax_cagr(x):+.1%} | "
              + ("" if k == "EW" else f"{ex.mean():+.2%}/month ({t:+.1f}) | {(yearly > 0).sum()}/{len(yearly)}")
              + ("| |" if k == "EW" else "") + f" | {(eq / eq.cummax() - 1).min():.0%} |")
