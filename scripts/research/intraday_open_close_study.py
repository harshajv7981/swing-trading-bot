"""Open-to-close rules on daily bars: docs/research/intraday-open-close-prereg.md.

    python scripts/research/intraday_open_close_study.py data/raw/eod2_data/daily

Writes runs/intraday-open-close/report.md. Rules, limits and pass bar are fixed in the prereg.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(sys.argv[1])
OUT = Path("runs/intraday-open-close/report.md")
COST, STOP_ATR, GAP, SLOTS, MIN_TV = 0.001, 1.0, 0.5, 5, 1e8
START, CUT, END = "2010-01-01", "2020-01-01", "2026-09-30"


def trades(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
    except (ValueError, pd.errors.ParserError):
        return None
    if "Series" not in df or len(df) < 300:
        return None
    df = df[df["Series"] == "EQ"]
    df = df[~df.index.duplicated()].sort_index()
    o, h, lo, c, v = (df[k].astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    pc = c.shift()
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().shift()  # known at the last close
    tv = (c * v).rolling(60).median().shift()
    stop = o - STOP_ATR * atr
    ret = np.where(lo <= stop, stop, c) / o - 1 - COST  # the stop always counts when the low reaches it
    t = pd.DataFrame({"ret": ret, "gap": (o - pc) / atr, "tv": tv,
                      "h20": pc >= c.rolling(20).max().shift()}, index=df.index)
    ok = (tv >= MIN_TV) & atr.gt(0) & ((o / pc - 1).abs() <= 0.25) & (o > 0)
    t = t[ok].loc[START:END].dropna()
    assert t["ret"].gt(-1).all(), path  # no trade can lose more than it put in
    return t.assign(sym=path.stem) if len(t) else None


def tstat(x: pd.Series) -> float:
    return x.mean() / x.std() * np.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else float("nan")


def main() -> None:
    parts = [t for p in sorted(DATA.glob("*.csv")) if (t := trades(p)) is not None]
    all_ = pd.concat(parts).rename_axis("date").reset_index()
    base_day = all_.groupby("date")["ret"].mean()
    rules = {"G-up": (all_[all_["gap"] >= GAP], "gap", False), "G-down": (all_[all_["gap"] <= -GAP], "gap", True),
             "H20": (all_[all_["h20"]], "tv", False)}
    days = base_day.index
    lines = ["# Open-to-close rules on daily bars: results", "",
             f"{all_['sym'].nunique()} stocks, {len(all_):,} stock-days, {days[0].date()} to {days[-1].date()}. "
             f"Costs {COST:.2%} a round trip, stop {STOP_ATR} ATR. Baseline: {base_day.mean() * 100:+.3f}% a trade.", "",
             "| Rule | Trades | Discovery: per trade vs baseline (t) | Holdout: 5-slot account per day (t) | "
             "Holdout: account CAGR | Holdout years beating baseline | Verdict |", "|---|---|---|---|---|---|---|"]
    for name, (sel, key, asc) in rules.items():
        picks = sel.sort_values(key, ascending=asc).groupby("date").head(SLOTS)
        acct = (picks.groupby("date")["ret"].sum() / SLOTS).reindex(days, fill_value=0.0)  # empty slots stay in cash
        ex = (picks.groupby("date")["ret"].mean() - base_day).dropna()  # per trade, on days the rule traded
        d, hd = ex[ex.index < CUT], ex[ex.index >= CUT]
        ha = acct[acct.index >= CUT]
        yrs = hd.groupby(hd.index.year).mean()
        cagr = (1 + ha).prod() ** (252 / len(ha)) - 1
        ok = d.mean() > 0 and tstat(d) > 2 and ha.mean() > 0 and (yrs > 0).sum() >= 4
        lines.append(f"| {name} | {len(picks):,} | {d.mean() * 100:+.3f}% ({tstat(d):+.1f}) | "
                     f"{ha.mean() * 100:+.3f}% ({tstat(ha):+.1f}) | {cagr * 100:+.1f}% | "
                     f"{(yrs > 0).sum()}/{len(yrs)} | {'**pass**' if ok else 'fail'} |")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
