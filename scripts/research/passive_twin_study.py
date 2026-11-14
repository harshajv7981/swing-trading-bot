"""M6 and G30 against their passive twins, after tax (docs/research/passive-twin-prereg.md).

    python scripts/research/passive_twin_study.py data/raw/eod2_data/daily momentum50_tri.csv

The CSV is niftyindices.com's historical Total Returns Index download for Nifty
Midcap150 Momentum 50 (a price-index download also works; say so in the doc).
M6 and gold are built exactly as in momentum_gold_study.py.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from momentum_gold_study import COST, GOLD_COST, GOLD_FILE, m6_and_ew  # noqa: E402
from weekly_rules_study import LTCG, STCG, after_tax_cagr  # noqa: E402

from swingbot.momentum import adjust_jumps, load  # noqa: E402

FEE = 0.004  # the fund's yearly cost, taken monthly
START, END, LIVE = "2010-01-01", "2026-10-01", "2022-09-01"
MARGIN = 0.01  # M6 or G30 must beat its twin by a point a year after tax, in both windows
ARMS = {  # name: ({sleeve: weight}, its twin)
    "M6": ({"m6": 1.0}, "Momentum 50 fund"),
    "G30": ({"m6": 0.7, "gold": 0.3}, "70/30 fund + gold"),
    "Momentum 50 fund": ({"fund": 1.0}, None),
    "70/30 fund + gold": ({"fund": 0.7, "gold": 0.3}, None),
}
SHORT = {"m6"}  # taxed each year at STCG; the rest at LTCG when sold


def month_ends(s: pd.Series, me: pd.DatetimeIndex) -> pd.Series:
    s = s[~s.index.duplicated()].sort_index().dropna().astype(float)
    return s.reindex(s.index.union(me)).ffill(limit=5).loc[me]


def read_index(path: Path) -> pd.Series:
    df = pd.read_csv(path)
    date = next(c for c in df.columns if "date" in c.lower())
    val = next(c for c in df.columns if "total return" in c.lower() or c.strip().lower() == "close")
    vals = pd.to_numeric(df[val].astype(str).str.replace(",", ""), errors="coerce")
    return pd.Series(vals.values, index=pd.to_datetime(df[date].str.strip(), format="mixed", dayfirst=True))


def simulate(rets: pd.DataFrame, target: dict[str, float], taxed: bool = True) -> tuple[float, pd.Series]:
    """Value of 1 at the end after selling everything and paying tax, and the month-end values before that.

    The M6 sleeve pays STCG on each calendar year's net gain each December (losses carried forward). The
    other sleeves pay LTCG on gains realised by the December trim, at average cost, and on the rest at the end."""
    st, lt = (STCG, LTCG) if taxed else (0.0, 0.0)
    v = dict(target)
    basis, year_start, carry, path = dict(v), {s: v[s] for s in v if s in SHORT}, 0.0, []

    def short_tax():
        nonlocal carry
        for s in year_start:
            gain = v[s] - year_start[s]
            if gain < 0:
                carry -= gain
            else:
                v[s] -= st * max(0.0, gain - carry)
                carry = max(0.0, carry - gain)

    for d, row in rets.iterrows():
        for s in v:
            v[s] *= 1 + row[s]
        if d.month == 12:
            short_tax()
            if len(v) > 1:
                total, paid = sum(v.values()), 0.0
                for s in v:
                    move = total * target[s] - v[s]
                    paid += abs(move) * (GOLD_COST if s == "gold" else COST)
                    if move < 0 and s not in SHORT:
                        paid += lt * max(0.0, -move / v[s] * (v[s] - basis[s]))
                after = total - paid
                for s in v:
                    new = after * target[s]
                    basis[s] = basis[s] * new / v[s] if new < v[s] else basis[s] + new - v[s]
                    v[s] = new
            year_start = {s: v[s] for s in year_start}
        path.append(sum(v.values()))
    if rets.index[-1].month != 12:
        short_tax()
    end = sum(v[s] - (0.0 if s in SHORT else lt * max(0.0, v[s] - basis[s])) for s in v)
    return end, pd.Series(path, index=rets.index)


def cagr_of(end: float, months: int) -> float:
    return end ** (12 / months) - 1


def main():
    data, tri = Path(sys.argv[1]), Path(sys.argv[2])
    px, val = load(data)
    me = px.groupby(px.index.to_period("M")).tail(1).index
    m6, _ = m6_and_ew(px, val, me)
    gold = month_ends(adjust_jumps(pd.read_csv(data / GOLD_FILE, usecols=["Date", "Close"], parse_dates=["Date"],
                                               index_col="Date")["Close"]), me).pct_change()
    fund = month_ends(read_index(tri), me).pct_change()
    fund = (1 + fund) * (1 - FEE) ** (1 / 12) - 1
    rets = pd.DataFrame({"m6": m6, "gold": gold, "fund": fund}).loc[START:END]
    rets = rets[rets.index < END]
    print(f"months {len(rets)}, missing: " + ", ".join(f"{c} {int(rets[c].isna().sum())}" for c in rets))
    rets = rets.fillna(0.0)

    # Self-check: M6 alone must reproduce the method behind its published after-tax figure.
    mine = cagr_of(simulate(rets[["m6"]], {"m6": 1.0})[0], len(rets))
    assert abs(mine - after_tax_cagr(rets["m6"], 12)) < 1e-9, (mine, after_tax_cagr(rets["m6"], 12))

    windows = {"full": rets, "live": rets.loc[LIVE:],
               "discovery (info)": rets.loc[:"2019-12-31"], "holdout (info)": rets.loc["2020-01-01":]}
    res = {}
    print("\n| Arm | Window | Months | CAGR before tax | After tax | Worst drawdown |\n|---|---|---|---|---|---|")
    for name, (target, _) in ARMS.items():
        for w, r in windows.items():
            pre = cagr_of(simulate(r[list(target)], target, taxed=False)[0], len(r))
            end, path = simulate(r[list(target)], target)
            res[name, w] = cagr_of(end, len(r))
            dd = (path / path.cummax() - 1).min()
            print(f"| {name} | {w} | {len(r)} | {pre:+.1%} | {res[name, w]:+.1%} | {dd:.0%} |")

    for name, (_, twin) in ARMS.items():
        if twin:
            gaps = {w: res[name, w] - res[twin, w] for w in ("full", "live")}
            ok = all(g >= MARGIN for g in gaps.values())
            print(f"\nVERDICT {name} vs {twin}: after-tax gap full {gaps['full']:+.1%}, live {gaps['live']:+.1%} "
                  f"(needs {MARGIN:+.0%} in both) -> {'keeps its case' if ok else 'the twin wins'}")


if __name__ == "__main__":
    main()
