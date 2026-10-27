"""Base-rate study for the signal-to-order thresholds on real NSE daily bars.

    git clone --depth 1 --filter=blob:none --sparse https://github.com/BennyThadikaran/eod2_data.git
    (cd eod2_data && git sparse-checkout set --no-cone /daily/reliance.csv ... /daily/niftybees.csv)
    python scripts/research/threshold_study.py eod2_data/daily runs/research/trades.parquet
    python scripts/research/threshold_tables.py runs/research/trades.parquet

Needs numba and pyarrow. The stock list is in docs/research/signal-thresholds.md.

Every stock-day is a hypothetical long call made after the close. The ticket is
the repo's: a DAY limit buy at the last close (fills only if the next session
trades at or below it), an ATR stop (stop-loss market: a gap fills at the open),
an R-multiple target, and a time exit at the close of session N counting the
entry day. Same-day stop and target: the stop is assumed first. Costs 0.25%
round trip. Result per trade in R (risk = planned entry - stop) and in %.
"""
import sys
from pathlib import Path

import numpy as np
from numba import njit
import pandas as pd

DATA = Path(sys.argv[1])
OUT = Path(sys.argv[2])
COST = 0.0025


def wilder(s, n):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def load(p):
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    df = df[df.get("Series", "EQ").fillna("EQ") == "EQ"] if "Series" in df else df
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df.columns = ["open", "high", "low", "close", "volume"]
    return df[~df.index.duplicated()].sort_index()


def features(df):
    c, h, l = df["close"], df["high"], df["low"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr, 14)
    d = c.diff()
    g, lo = wilder(d.clip(lower=0), 14), wilder(-d.clip(upper=0), 14)
    rsi = 100 - 100 / (1 + g / lo)
    f = pd.DataFrame({
        "close": c, "atr": atr, "rsi": rsi,
        "sma50": c.rolling(50).mean(), "sma200": c.rolling(200).mean(),
        "ema10": c.ewm(span=10, adjust=False, min_periods=10).mean(),
        "value_cr": (c * df["volume"]).rolling(20).mean() / 1e7,
    })
    f["ext"] = (f["close"] - f["ema10"]) / f["atr"]
    f["above200"] = f["close"] >= f["sma200"]
    f["above50"] = f["close"] >= f["sma50"]
    return f


def simulate(df, f, k_atr, r_mult, hold):
    """One row per signal day: R and % result, or NaN if the limit never filled."""
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    return _sim(o, h, l, c, f["close"].to_numpy(), f["atr"].to_numpy(), k_atr, r_mult, hold)


@njit(cache=True)
def _sim(o, h, l, c, close, atr, k_atr, r_mult, hold):
    n = len(o)
    res_r = np.full(n, np.nan)
    res_p = np.full(n, np.nan)
    for t in range(n - 1):
        a = atr[t]
        if not a > 0:
            continue
        limit = close[t]
        stop = limit - k_atr * a
        target = limit + r_mult * (limit - stop)
        j = t + 1
        if l[j] > limit:  # never traded down to the limit
            continue
        fill = min(o[j], limit)
        exit_px = -1.0
        last = min(t + hold, n - 1)  # session N counts the entry day as 1
        for i in range(j, last + 1):
            if l[i] <= stop:
                exit_px = min(o[i], stop) if i > j else stop
                break
            if h[i] >= target:
                exit_px = max(o[i], target) if i > j else target
                break
        if exit_px < 0:
            if t + hold > n - 1:
                continue  # hold window not finished
            exit_px = c[last]
        pct = exit_px / fill - 1 - COST
        res_p[t] = pct
        res_r[t] = pct * fill / (limit - stop)
    return res_r, res_p


def main():
    nifty = load(DATA / "niftybees.csv")
    nf = features(nifty)
    mkt = pd.DataFrame({"mkt_above200": nf["above200"],
                        "mkt_ret20": nifty["close"].pct_change(20)})
    rows = []
    grid = [(k, r, hd) for k in (1.5, 2.0, 2.5, 3.0) for r in (1.5, 2.0, 3.0) for hd in (3, 5, 10, 20)]
    files = sorted(p for p in DATA.glob("*.csv") if p.stem != "niftybees")
    for p in files:
        df = load(p)
        df = df[df.index >= "2008-01-01"]
        if len(df) < 400:
            continue
        f = features(df).join(mkt, how="left")
        base = f.assign(ticker=p.stem.upper())
        for k, r, hd in grid:
            rr, pp = simulate(df, f, k, r, hd)
            part = base.assign(k=k, rm=r, hold=hd, R=rr, pct=pp)
            rows.append(part[(part.index >= "2010-01-01") & part["R"].notna()])
        print(p.stem, file=sys.stderr)
    out = pd.concat(rows)
    out.index.name = "date"
    out.reset_index().to_parquet(OUT)
    print(len(out))


if __name__ == "__main__":
    main()
