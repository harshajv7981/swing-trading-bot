"""Stop-losses on M6 (docs/research/m6-stop-loss-prereg.md).

    python scripts/research/m6_stop_loss_study.py data/raw/eod2_data/daily

M6 is m6_and_ew in momentum_gold_study.py. S2 and S3 add a stop at the month-end
close minus 2 or 3 x ATR(14), checked on each later day of the month with
paper.ledger.stop_fill; a stopped name sits in cash until the next month-end.
Stops apply only to the 60 months ending Nov 2021 - Oct 2026; earlier months run
plain M6 so every arm enters the window from the same state.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from momentum_gold_study import COST, HI, LO, MIN_VALUE, TOP, cagr, equal, m6_and_ew, maxdd  # noqa: E402
from passive_twin_study import FEE, month_ends, read_index  # noqa: E402

from swingbot.momentum import load  # noqa: E402
from swingbot.paper.ledger import stop_fill  # noqa: E402
from swingbot.research import after_tax_cagr, hold_after_tax_cagr  # noqa: E402
from swingbot.technicals import _wilder, true_range  # noqa: E402

START, END = "2021-11-01", "2026-10-31"  # month-ends whose return is judged
ATR_DAYS, MIN_BARS = 14, 15
ARMS = {"A, M6 (no stop)": None, "S2 (2x ATR)": 2.0, "S3 (3x ATR)": 3.0}
INDEX = Path("data/raw/indices/momentum50_tri.csv")
DD_BAR, COST_BAR = 0.05, 0.01


class Bars:
    """Daily OHLC from the raw files, scaled by adjusted close / raw close so stops sit on load()'s prices."""

    def __init__(self, data: Path, px: pd.DataFrame):
        self.data, self.px, self.cache = data, px, {}

    def __call__(self, sym: str) -> pd.DataFrame | None:
        if sym not in self.cache:
            self.cache[sym] = self._read(sym)
        return self.cache[sym]

    def _read(self, sym):
        p = self.data / f"{sym.lower()}.csv"
        if not p.exists():
            return None
        df = pd.read_csv(p, usecols=["Date", "Open", "High", "Low", "Close", "Series"], parse_dates=["Date"],
                         index_col="Date")
        df = df[df["Series"].fillna("EQ") == "EQ"]
        df = df[~df.index.duplicated()].sort_index().rename(columns=str.lower)[["open", "high", "low", "close"]]
        factor = self.px[sym].reindex(df.index) / df["close"].astype(float)
        return df.astype(float).mul(factor, axis=0).dropna()


def stop_level(bars: pd.DataFrame, a: pd.Timestamp, close: float, k: float) -> float | None:
    past = bars[bars.index <= a].tail(120)
    if len(past) < MIN_BARS:
        return None
    atr = _wilder(true_range(past), ATR_DAYS).iloc[-1]
    return None if pd.isna(atr) else close - k * float(atr)


def m6_with_stop(px, val, me_dates, bars, k):
    """M6's monthly net returns with a k x ATR stop (k None: no stop), and one info row per stopped name."""
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]
    held, rows, stops, counts = pd.Series(dtype=float), [], [], []
    for i in range(6, len(me_dates) - 1):
        a, nxt = me_dates[i], me_dates[i + 1]
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        names = medval.loc[a][ok].sort_values(ascending=False).index[LO:HI]
        r = (me_px.loc[nxt] / me_px.loc[a] - 1).fillna(0.0)
        score = (me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(names).dropna()
        target = equal(score.nlargest(TOP).index)
        allk = target.index.union(held.index)
        diff = target.reindex(allk, fill_value=0) - held.reindex(allk, fill_value=0)
        # Held weights sum below 1 when a stop left cash; the buys then exceed the sells.
        # The very first month m6_and_ew charges 0.5 x the initial buy; keep that so the self-check holds.
        turn = max(diff.clip(lower=0).sum(), (-diff).clip(lower=0).sum()) if len(held) else 0.5 * diff.abs().sum()
        rr = r.reindex(target.index).fillna(0.0)
        stopped = set()
        if k is not None and pd.Timestamp(START) <= nxt <= pd.Timestamp(END):
            for sym in target.index:
                b = bars(sym)
                close = me_px.loc[a, sym]
                level = stop_level(b, a, close, k) if b is not None else None
                if level is None:
                    continue
                for day, bar in b[(b.index > a) & (b.index <= nxt)].iterrows():
                    fill = stop_fill(level, bar["open"], bar["low"])
                    if fill is not None:
                        rr[sym] = fill / close * (1 - COST) - 1
                        stopped.add(sym)
                        after = me_px.loc[nxt, sym] / fill - 1
                        stops.append({"month": nxt, "sym": sym, "gap": bool(bar["open"] <= level),
                                      "after": after})
                        break
            counts.append((nxt, len(stopped) / len(target)))
        gross = float((target * rr).sum())
        rows.append((nxt, gross - COST * turn))
        held = (target * (1 + rr) / (1 + gross)).drop(list(stopped))
    return pd.Series(dict(rows)), pd.DataFrame(stops), pd.Series(dict(counts), dtype=float)


def main():
    data = Path(sys.argv[1])
    px, val = load(data)
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    bars = Bars(data, px)

    m6_ref, _ = m6_and_ew(px, val, me_dates)
    res = {name: m6_with_stop(px, val, me_dates, bars, k) for name, k in ARMS.items()}
    # Self-check: with no stop the arm is M6 exactly.
    base = res["A, M6 (no stop)"][0]
    assert np.allclose(base.values, m6_ref.reindex(base.index).values, atol=1e-12), "no-stop arm != m6_and_ew"

    win = {name: r[0].loc[START:END] for name, r in res.items()}
    n = len(win["A, M6 (no stop)"])
    print(f"Window {win['A, M6 (no stop)'].index[0].date()} - {win['A, M6 (no stop)'].index[-1].date()}, {n} months\n")
    print("| Arm | CAGR | After tax | Worst drawdown | Worst month | Stopped/month | Gaps | Stopped name, fill to month-end |")
    print("|---|---|---|---|---|---|---|---|")
    stats = {}
    for name, (_, stops, counts) in res.items():
        x = win[name]
        stats[name] = (after_tax_cagr(x), maxdd(x))
        if len(stops):
            info = (f"{counts.mean():.1%} | {int(stops['gap'].sum())} of {len(stops)} | "
                    f"{stops['after'].mean():+.1%} (median {stops['after'].median():+.1%})")
        else:
            info = "– | – | –"
        print(f"| {name} | {cagr(x):+.1%} | {stats[name][0]:+.1%} | {stats[name][1]:.1%} | {x.min():+.1%} | {info} |")

    if INDEX.exists():
        fund = month_ends(read_index(INDEX), me_dates).pct_change()
        fund = ((1 + fund) * (1 - FEE) ** (1 / 12) - 1).loc[START:END]
        print(f"| Momentum 50 fund (info) | {cagr(fund):+.1%} | {hold_after_tax_cagr(fund):+.1%} (held, LTCG) | "
              f"{maxdd(fund):.1%} | {fund.min():+.1%} | – | – | – |")
    else:
        print(f"(Momentum 50 index file {INDEX} not found: fund comparison skipped)")

    a_tax, a_dd = stats["A, M6 (no stop)"]
    passed = []
    print()
    for name in ("S2 (2x ATR)", "S3 (3x ATR)"):
        tax, dd = stats[name]
        dd_ok, cost_ok = dd - a_dd >= DD_BAR - 1e-12, tax >= a_tax - COST_BAR - 1e-12
        print(f"{name}: drawdown {100 * (dd - a_dd):+.1f} pts vs A (needs +5.0) {'PASS' if dd_ok else 'FAIL'}; "
              f"after tax {100 * (tax - a_tax):+.1f} pts vs A (needs >= -1.0) {'PASS' if cost_ok else 'FAIL'}")
        if dd_ok and cost_ok:
            passed.append(name)
    if not passed:
        print("Verdict: neither stop passes; stops stay information only.")
    else:
        pick = "S3 (3x ATR)" if len(passed) == 2 and stats["S3 (3x ATR)"][0] > stats["S2 (2x ATR)"][0] else \
            ("S2 (2x ATR)" if "S2 (2x ATR)" in passed else passed[0])
        print(f"Verdict: {', '.join(passed)} pass(es); chosen: {pick}.")


if __name__ == "__main__":
    main()
