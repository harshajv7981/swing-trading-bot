"""Mid-cap momentum drawdown control (docs/research/momentum-drawdown-prereg.md).

    python scripts/research/momentum_drawdown_study.py eod2_data/daily

Arms on the mid bucket (ranks 101-250 by traded value, rebuilt monthly):
A monthly M6, B A plus an 8-month trend filter on the bucket's EW index,
C quarterly M6, and B+C (information only). Weights drift between rebalances.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot.momentum import load

LO, HI = 100, 250
TOP = 20
COST = 0.0040  # per unit of one-way turnover, as in momentum_broad_study.py
MIN_VALUE = 5e7  # Rs 5 crore
CASH_MONTH = 1.06 ** (1 / 12) - 1  # liquid fund, 6% a year
STCG, LTCG, FUND_FEE = 0.20, 0.125, 0.004
INDEX_FILE = "nifty midcap150 momentum 50.csv"
QUARTER_ENDS = (3, 6, 9, 12)
ARMS = {  # name: (filter months or None, quarterly)
    "A": (None, False), "B": (8, False), "C": (None, True), "B+C": (8, True),
    "B6 (sensitivity)": (6, False), "B10 (sensitivity)": (10, False),
}
JUDGED = ("B", "C")


def cagr(x):
    return (1 + x).prod() ** (12 / len(x)) - 1


def maxdd(x):
    eq = (1 + x).cumprod()
    return (eq / eq.cummax() - 1).min()


def after_tax_stcg(x):
    """CAGR after 20% tax each calendar year on net gains, losses carried forward."""
    w, carry = 1.0, 0.0
    for _, s in x.groupby(x.index.year):
        start = w
        w *= (1 + s).prod()
        gain = w - start
        if gain < 0:
            carry -= gain
        else:
            used = min(carry, gain)
            carry -= used
            w -= STCG * (gain - used)
    return w ** (12 / len(x)) - 1


def after_tax_fund(x):
    """CAGR of an index fund: annual fee, then 12.5% on the whole gain at sale."""
    w = ((1 + x) * (1 - FUND_FEE / 12)).prod()
    w -= LTCG * max(w - 1, 0)
    return w ** (12 / len(x)) - 1


def main():
    data = Path(sys.argv[1])
    px, val = load(data)
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]

    ew_level = [1.0]  # EW index at each month-end, starting at me_dates[0]
    ew_prev = pd.Series(dtype=float)
    held = {k: pd.Series(dtype=float) for k in ARMS}  # drifted weights; "CASH" is a position
    rows = {k: [] for k in ARMS}
    ew_rows = []
    for i in range(len(me_dates) - 1):
        a, nxt = me_dates[i], me_dates[i + 1]
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        names = medval.loc[a][ok].sort_values(ascending=False).index[LO:HI]
        # No price a month later (suspended): counted flat, as before.
        r = (me_px.loc[nxt] / me_px.loc[a] - 1).fillna(0.0)
        r["CASH"] = CASH_MONTH

        ew = pd.Series(1 / len(names), index=names) if len(names) else pd.Series(dtype=float)
        allk = ew.index.union(ew_prev.index)
        turn = 0.5 * (ew.reindex(allk, fill_value=0) - ew_prev.reindex(allk, fill_value=0)).abs().sum()
        ew_ret = float((ew * r.reindex(ew.index)).sum()) - COST * turn
        ew_prev = ew
        ew_rows.append((nxt, ew_ret))

        picks = pd.Index([])
        if i >= 6:
            score = (me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(names).dropna()
            picks = score.nlargest(TOP).index
        for k, (months, quarterly) in ARMS.items():
            cur = held[k]
            invested = len(cur) and "CASH" not in cur.index
            if months and len(ew_level) >= months and ew_level[-1] < np.mean(ew_level[-months:]):
                target = pd.Series({"CASH": 1.0})
            elif quarterly and a.month not in QUARTER_ENDS and invested:
                target = cur  # hold the drifting book between quarter-ends
            elif len(picks):
                target = pd.Series(1 / len(picks), index=picks)
            else:
                target = pd.Series(dtype=float)
            allk = target.index.union(cur.index)
            turn = 0.5 * (target.reindex(allk, fill_value=0) - cur.reindex(allk, fill_value=0)).abs().sum()
            rr = r.reindex(target.index).fillna(0.0)
            gross = float((target * rr).sum())
            rows[k].append((nxt, gross - COST * turn, turn, "CASH" in target.index))
            held[k] = target * (1 + rr) / (1 + gross) if len(target) else target
        ew_level.append(ew_level[-1] * (1 + ew_ret))

    def window(df):
        return df[(df.index >= "2010-01-01") & (df.index < "2026-10-01")]

    ew_s = window(pd.DataFrame(ew_rows, columns=["date", "ret"]).set_index("date"))["ret"]
    res = {k: window(pd.DataFrame(v, columns=["date", "ret", "turn", "cash"]).set_index("date"))
           for k, v in rows.items()}

    idx = pd.read_csv(data / INDEX_FILE, usecols=["Date", "Close"], parse_dates=["Date"],
                      index_col="Date")["Close"].sort_index()
    idx = idx[~idx.index.duplicated()]
    idx_me = idx.reindex(idx.index.union(me_dates)).ffill(limit=5).loc[me_dates]
    idx_ret = window((idx_me / idx_me.shift() - 1).dropna())  # dated by the month-end it ends on
    span = f"{idx_ret.index[0]:%b %Y} - {idx_ret.index[-1]:%b %Y}"

    print(f"Mid-bucket EW: CAGR {cagr(ew_s):+.1%}, max dd {maxdd(ew_s):.1%}")
    print(f"Momentum 50 index ({span}, {len(idx_ret)} months): price CAGR {cagr(idx_ret):+.1%}, "
          f"max dd {maxdd(idx_ret):.1%}, as a fund after fee and 12.5% LTCG {after_tax_fund(idx_ret):+.1%}")
    fund_bar = after_tax_fund(idx_ret)
    base = {}
    for k, df in res.items():
        x = df["ret"]
        ex = x - ew_s
        per = {"discovery": ex[ex.index.year <= 2019], "holdout": ex[ex.index.year >= 2020]}
        stats = {p: (e.mean(), e.mean() / e.std() * np.sqrt(len(e))) for p, e in per.items()}
        sub = x.reindex(idx_ret.index)
        tax_all, tax_sub = after_tax_stcg(x), after_tax_stcg(sub)
        dd = maxdd(x)
        if k == "A":
            base = {"dd": dd, **{p: s[0] for p, s in stats.items()}}
        print(f"\n######## {k}")
        print(f"CAGR {cagr(x):+.1%}, max dd {dd:.1%}, turnover {df['turn'].mean():.0%}/month, "
              f"months in cash {int(df['cash'].sum())}/{len(df)}")
        for p, (m, t) in stats.items():
            print(f"  {p}: excess {m:+.2%}/month (t {t:+.1f}, n {len(per[p])})")
        print(f"  after 20% STCG: {tax_all:+.1%} (full period); {tax_sub:+.1%} over {span} "
              f"vs fund {fund_bar:+.1%} -> {'beats' if tax_sub > fund_bar else 'trails'} the fund")
        yearly = x.groupby(x.index.year).apply(lambda s: (1 + s).prod() - 1)
        print("  yearly: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))
        if k in JUDGED:
            c1 = dd >= base["dd"] * (2 / 3)
            c2 = all(stats[p][0] >= base[p] - 0.002 for p in ("discovery", "holdout"))
            c3 = tax_sub > fund_bar
            print(f"  VERDICT: drawdown {'pass' if c1 else 'fail'} (needs {base['dd'] * 2 / 3:.1%} or better), "
                  f"return kept {'pass' if c2 else 'fail'}, beats fund {'pass' if c3 else 'fail'} "
                  f"-> {'PASS' if c1 and c2 and c3 else 'FAIL'}")
    yi = idx_ret.groupby(idx_ret.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("\nMomentum 50 index yearly: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yi.items()))
    ye = ew_s.groupby(ew_s.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("Mid EW yearly: " + ", ".join(f"{y}:{v:+.0%}" for y, v in ye.items()))


if __name__ == "__main__":
    main()
