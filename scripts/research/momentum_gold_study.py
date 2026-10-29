"""Mid-cap momentum plus gold (docs/research/momentum-gold-prereg.md).

    python scripts/research/momentum_gold_study.py eod2_data/daily

M6 is arm A of momentum_lowvol_study.py: mid bucket (ranks 101-250 by traded
value, rebuilt monthly), top 20 by 6-1 month return, monthly, drifting weights.
Gold is GOLDBEES, jump-adjusted. Mixes rebalance to target each December.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot.momentum import adjust_jumps, load

LO, HI = 100, 250
TOP = 20
COST = 0.0040  # per unit of one-way equity turnover, as in momentum_broad_study.py
GOLD_COST = 0.0010  # per unit of gold moved at a rebalance
MIN_VALUE = 5e7  # Rs 5 crore
CASH_MONTH = 1.06 ** (1 / 12) - 1
GOLD_FILE = "goldbees.csv"
MIXES = {  # name: (M6, gold, cash) target weights
    "A (100% M6)": (1.0, 0.0, 0.0),
    "G30 (70/30, judged)": (0.7, 0.3, 0.0),
    "G20 (80/20)": (0.8, 0.2, 0.0),
    "G40 (60/40)": (0.6, 0.4, 0.0),
    "60/20/20 M6/gold/cash": (0.6, 0.2, 0.2),
    "Gold alone": (0.0, 1.0, 0.0),
}
JUDGED = "G30 (70/30, judged)"


def cagr(x):
    return (1 + x).prod() ** (12 / len(x)) - 1


def maxdd(x):
    eq = (1 + x).cumprod()
    return (eq / eq.cummax() - 1).min()


def sharpe(x):
    ex = x - CASH_MONTH
    return ex.mean() / ex.std() * np.sqrt(12)


def equal(names):
    return pd.Series(1 / len(names), index=names) if len(names) else pd.Series(dtype=float)


def m6_and_ew(px, val, me_dates):
    """Monthly net returns of mid-cap M6 and the mid-bucket EW, dated by the month-end they end on."""
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]
    ew_prev, held = pd.Series(dtype=float), pd.Series(dtype=float)
    ew_rows, m6_rows = [], []
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
        target = equal(score.nlargest(TOP).index)
        allk = target.index.union(held.index)
        turn = 0.5 * (target.reindex(allk, fill_value=0) - held.reindex(allk, fill_value=0)).abs().sum()
        rr = r.reindex(target.index).fillna(0.0)
        gross = float((target * rr).sum())
        m6_rows.append((nxt, gross - COST * turn))
        held = target * (1 + rr) / (1 + gross)
    return (pd.Series(dict(m6_rows)), pd.Series(dict(ew_rows)))


def mix(m6, gold, weights):
    """Sleeves drift during the year and go back to target at each December month-end."""
    target = np.array(weights)
    w = target.copy()
    out = []
    for d in m6.index:
        r = np.array([m6[d], gold[d], CASH_MONTH])
        gross = float(w @ r)
        w = w * (1 + r) / (1 + gross)
        cost = 0.0
        if d.month == 12:
            moved = np.abs(target - w)
            cost = COST * moved[0] + GOLD_COST * moved[1]
            w = target.copy()
        out.append(gross - cost)
    return pd.Series(out, index=m6.index)


def main():
    data = Path(sys.argv[1])
    px, val = load(data)
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    m6, ew = m6_and_ew(px, val, me_dates)

    g = pd.read_csv(data / GOLD_FILE, usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
    g = adjust_jumps(g[~g.index.duplicated()].sort_index().dropna().astype(float))
    g_me = g.reindex(g.index.union(me_dates)).ffill(limit=5).loc[me_dates]
    gold = (g_me / g_me.shift() - 1)

    keep = (m6.index >= "2010-01-01") & (m6.index < "2026-10-01")
    m6 = m6[keep]
    ew = ew.reindex(m6.index)
    gold = gold.reindex(m6.index)
    print(f"months {len(m6)}, gold months missing {int(gold.isna().sum())}; "
          f"corr(M6, gold) {m6.corr(gold):+.2f}, corr(EW, gold) {ew.corr(gold):+.2f}")
    gold = gold.fillna(0.0)

    def report(name, x):
        d, h = x[x.index.year <= 2019], x[x.index.year >= 2020]
        yearly = x.groupby(x.index.year).apply(lambda s: (1 + s).prod() - 1)
        print(f"\n######## {name}")
        print(f"CAGR {cagr(x):+.1%}, max dd {maxdd(x):.1%}, Sharpe {sharpe(x):.2f}")
        print(f"  discovery 2010-19: CAGR {cagr(d):+.1%}, Sharpe {sharpe(d):.2f}, max dd {maxdd(d):.1%}")
        print(f"  holdout 2020-26: CAGR {cagr(h):+.1%}, Sharpe {sharpe(h):.2f}, max dd {maxdd(h):.1%}")
        print("  yearly: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))
        return x

    report("Mid-bucket EW", ew)
    res = {k: report(k, mix(m6, gold, w)) for k, w in MIXES.items()}

    a, g30 = res["A (100% M6)"], res[JUDGED]
    periods = {"discovery": lambda s: s[s.index.year <= 2019], "holdout": lambda s: s[s.index.year >= 2020]}
    c1 = maxdd(g30) >= maxdd(a) * (2 / 3)
    c2 = all(sharpe(p(g30)) > sharpe(p(a)) for p in periods.values())
    c3 = all(cagr(p(g30)) > cagr(p(ew)) for p in periods.values())
    print(f"\nVERDICT {JUDGED}: drawdown {'pass' if c1 else 'fail'} (needs {maxdd(a) * 2 / 3:.1%} or better, "
          f"has {maxdd(g30):.1%}), Sharpe above A in both {'pass' if c2 else 'fail'}, "
          f"CAGR above EW in both {'pass' if c3 else 'fail'} -> {'PASS' if c1 and c2 and c3 else 'FAIL'}")


if __name__ == "__main__":
    main()
