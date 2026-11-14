"""Rank-buffer M6, R40 (docs/research/rank-buffer-prereg.md).

    python scripts/research/rank_buffer_study.py data/raw/eod2_data/daily data/raw/indices/momentum50_tri.csv

M6 exactly as momentum_gold_study.m6_and_ew, except that a holding is kept while it ranks within the top
KEEP of the bucket. With KEEP = TOP the rule is M6, which the script checks before judging.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from momentum_gold_study import COST, HI, LO, MIN_VALUE, TOP, equal, m6_and_ew  # noqa: E402
from passive_twin_study import FEE, LIVE, month_ends, read_index, simulate  # noqa: E402
from weekly_rules_study import after_tax_cagr  # noqa: E402

from swingbot.momentum import load  # noqa: E402

KEEP = 40
START, END = "2010-01-01", "2026-10-01"


def buffered(px, val, me_dates, keep):
    """Monthly net returns and one-way turnover of top-TOP momentum, holding names while they rank <= keep."""
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]
    held, rows = pd.Series(dtype=float), []
    for i in range(6, len(me_dates) - 1):
        a, nxt = me_dates[i], me_dates[i + 1]
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        names = medval.loc[a][ok].sort_values(ascending=False).index[LO:HI]
        r = (me_px.loc[nxt] / me_px.loc[a] - 1).fillna(0.0)
        ranked = (me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(names).dropna().sort_values(ascending=False)
        stay = [s for s in ranked.index[:keep] if s in held.index]
        target = equal(stay + [s for s in ranked.index if s not in stay][:TOP - len(stay)])
        allk = target.index.union(held.index)
        turn = 0.5 * (target.reindex(allk, fill_value=0) - held.reindex(allk, fill_value=0)).abs().sum()
        rr = r.reindex(target.index).fillna(0.0)
        gross = float((target * rr).sum())
        rows.append((nxt, gross - COST * turn, turn))
        held = target * (1 + rr) / (1 + gross)
    df = pd.DataFrame(rows, columns=["d", "ret", "turn"]).set_index("d")
    return df["ret"], df["turn"]


def t(x):
    return x.mean() / x.std() * np.sqrt(len(x))


def main():
    data, tri = Path(sys.argv[1]), Path(sys.argv[2])
    px, val = load(data)
    me = px.groupby(px.index.to_period("M")).tail(1).index
    m6, ew = m6_and_ew(px, val, me)
    same, _ = buffered(px, val, me, TOP)
    assert np.allclose(same.reindex(m6.index), m6), "KEEP = TOP must reproduce M6"
    r40, turn = buffered(px, val, me, KEEP)
    _, turn_a = buffered(px, val, me, TOP)

    def cut(x, lo=START, hi=END):
        return x[(x.index >= lo) & (x.index < hi)]

    arms = {"A, M6": (cut(m6), cut(turn_a)), f"R{KEEP}": (cut(r40), cut(turn))}
    periods = {"discovery 2010-19": (START, "2020-01-01"), "holdout 2020-26": ("2020-01-01", END)}
    res = {}
    print("| Arm | Period | CAGR | After tax | Excess over EW (t) | Worst drawdown | Turnover/month |")
    print("|---|---|---|---|---|---|---|")
    for name, (x, tv) in arms.items():
        for p, (lo, hi) in {"full": (START, END), **periods}.items():
            s, ex = cut(x, lo, hi), cut(x - ew, lo, hi)
            v = (1 + s).cumprod()
            res[name, p] = after_tax_cagr(s, 12), t(ex)
            print(f"| {name} | {p} | {v.iloc[-1] ** (12 / len(s)) - 1:+.1%} | {res[name, p][0]:+.1%} | "
                  f"{ex.mean():+.2%} ({res[name, p][1]:+.1f}) | {(v / v.cummax() - 1).min():.0%} | "
                  f"{cut(tv, lo, hi).mean():.0%} |")

    fund = month_ends(read_index(tri), me).pct_change()
    fund = (1 + fund) * (1 - FEE) ** (1 / 12) - 1
    rets = pd.DataFrame({"m6": r40, "fund": fund}).loc[START:].fillna(0.0)
    rets = rets[rets.index < END]
    for w, r in {"full": rets, "live": rets.loc[LIVE:]}.items():
        a = simulate(r[["m6"]], {"m6": 1.0})[0] ** (12 / len(r)) - 1
        b = simulate(r[["fund"]], {"fund": 1.0})[0] ** (12 / len(r)) - 1
        print(f"info: R{KEEP} vs Momentum 50 fund, {w} ({len(r)} months), after tax: {a:+.1%} vs {b:+.1%}")

    gaps = {p: res[f"R{KEEP}", p][0] - res["A, M6", p][0] for p in periods}
    c1 = all(g >= 0.005 for g in gaps.values())
    c2 = res[f"R{KEEP}", "discovery 2010-19"][1] >= 2
    print(f"\nVERDICT R{KEEP}: after-tax gap vs M6 " + ", ".join(f"{p} {g:+.1%}" for p, g in gaps.items())
          + f" (needs +0.5% in both) {'pass' if c1 else 'fail'}; discovery t "
          f"{res[f'R{KEEP}', 'discovery 2010-19'][1]:+.1f} (needs 2) {'pass' if c2 else 'fail'} -> "
          f"{'PASS' if c1 and c2 else 'FAIL'}")


if __name__ == "__main__":
    main()
