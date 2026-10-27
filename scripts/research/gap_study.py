"""Entry rule study: a DAY limit at the last close, or buy at the open?

    python scripts/research/gap_study.py eod2_data/daily 3     # hold 3 sessions
    python scripts/research/gap_study.py eod2_data/daily 5

Same data as threshold_study.py (eod2_data daily bars, Nifty 100 names). Every
stock-day from 2010 is a long call made after the close, traded two ways with
a 2.5-ATR stop, a 2R target and a time exit at the close of session N:

    open   buy at the next open; the stop and target are planned from the fill
    limit  the bot's ticket: a DAY limit at the close, filled at the open if it
           opens at or under it, else at the limit if the low reaches it

Results are bucketed by the next open's gap over the close, in ATRs, as a
stand-in for overnight news. Costs 0.25% round trip. Prints one table per
period; "limit_R_per_call" counts an unfilled limit as 0R (the cash stays put).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA, HOLD = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 3
COST, K, RM = 0.0025, 2.5, 2.0
BINS = [-99, -0.5, 0, 0.25, 0.5, 1, 1.5, 2, 99]


def load(p):
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")[["Open", "High", "Low", "Close"]].astype(float)
    df.columns = ["o", "h", "l", "c"]
    return df[~df.index.duplicated()].sort_index()


def atr(df):
    c = df["c"]
    tr = pd.concat([df["h"] - df["l"], (df["h"] - c.shift()).abs(), (df["l"] - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()


def exit_price(o, h, l, c, j, stop, target, last):
    for i in range(j, last + 1):
        if l[i] <= stop:
            return min(o[i], stop) if i > j else stop
        if h[i] >= target:
            return max(o[i], target) if i > j else target
    return c[last]


def trades():
    rows = []
    for p in sorted(DATA.glob("*.csv")):
        if p.stem == "niftybees":
            continue
        df = load(p)
        df = df[df.index >= "2008-01-01"]
        if len(df) < 400:
            continue
        a = atr(df).to_numpy()
        o, h, l, c = (df[x].to_numpy() for x in "ohlc")
        for t in range(200, len(o) - HOLD):
            if not a[t] > 0 or df.index[t].year < 2010:
                continue
            j, last = t + 1, t + HOLD
            fill = o[j]
            stop = fill - K * a[t]
            r_open = (exit_price(o, h, l, c, j, stop, fill + RM * K * a[t], last) / fill - 1 - COST) * fill / (fill - stop)
            limit = c[t]
            stop = limit - K * a[t]
            r_limit = np.nan
            if l[j] <= limit:
                fill = min(o[j], limit)
                x = exit_price(o, h, l, c, j, stop, limit + RM * K * a[t], last)
                r_limit = (x / fill - 1 - COST) * fill / (limit - stop)
            rows.append((df.index[t].year, (o[j] - c[t]) / a[t], r_open, r_limit))
    return pd.DataFrame(rows, columns=["year", "gap", "open_R", "limit_R"])


def main():
    d = trades()
    d["gap_atr"] = pd.cut(d["gap"], BINS)
    for name, part in (("2010-19", d[d["year"] <= 2019]), ("2020-26", d[d["year"] >= 2020])):
        g = part.groupby("gap_atr", observed=True)
        print(f"\nhold {HOLD} sessions, {name} (n={len(part)})")
        print(pd.DataFrame({
            "share%": (g.size() / len(part) * 100).round(1),
            "open_R": g["open_R"].mean().round(3),
            "limit_fill%": (g["limit_R"].apply(lambda s: s.notna().mean()) * 100).round(0),
            "limit_R_per_call": g["limit_R"].apply(lambda s: s.fillna(0).mean()).round(3),
        }).to_string())


if __name__ == "__main__":
    main()
