"""Pre-registered price-rule study (docs/research/price-rules-prereg.md).

    python scripts/research/price_rules_study.py eod2_data/daily runs/research/price_rules.parquet

Reuses the ticket simulator from threshold_study.py (limit at the close, 2.5 ATR
stop, 2R target, time exit), at hold 5 and hold 10. Writes one row per trade taken,
with the rule that fired; one position per stock per rule at a time.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("ts", HERE / "threshold_study.py")
_argv, sys.argv = sys.argv, [sys.argv[0], "x", "y"]  # threshold_study reads argv at import
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)
sys.argv = _argv

K_ATR, R_MULT = 2.5, 2.0


def rsi(c, n):
    d = c.diff()
    g, lo = ts.wilder(d.clip(lower=0), n), ts.wilder(-d.clip(upper=0), n)
    return 100 - 100 / (1 + g / lo)


def signals(df, f):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    ret = c.pct_change()
    vol_x = v / v.rolling(20).mean().shift()
    pos = (c - l) / (h - l).replace(0, np.nan)
    above = f["above200"]
    return {
        "A_thrust": (ret >= 0.03) & (vol_x >= 2) & (pos >= 0.7) & above,
        "B_pullback": above & (rsi(c, 2) < 10),
        "C_52w_high": (c >= c.shift().rolling(250).max()) & (vol_x >= 1.5),
        "D_shock_dip": (ret <= -0.04) & (vol_x >= 2) & above,
        "ALL": pd.Series(True, index=c.index),
        "TREND": above.astype(bool),
    }


def one_per_window(mask, hold):
    """Keep a signal only if the last one kept is at least `hold` sessions back."""
    idx = np.flatnonzero(mask)
    keep, last = [], -10**9
    for i in idx:
        if i - last >= hold:
            keep.append(i)
            last = i
    out = np.zeros(len(mask), dtype=bool)
    out[keep] = True
    return out


def main():
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    nifty = ts.load(data / "niftybees.csv")
    mkt_above = ts.features(nifty)["above200"].rename("mkt_above200")
    rows = []
    for p in sorted(x for x in data.glob("*.csv") if x.stem != "niftybees"):
        df = ts.load(p)
        df = df[df.index >= "2008-01-01"]
        if len(df) < 400:
            continue
        f = ts.features(df)
        sig = signals(df, f)
        for hold in (5, 10):
            rr, pp = ts.simulate(df, f, K_ATR, R_MULT, hold)
            for name, m in sig.items():
                m = m.fillna(False).to_numpy() & (df.index >= "2010-01-01")
                if name in ("ALL", "TREND"):
                    take = m  # base rates: every day, overlapping is fine for a mean
                else:
                    take = one_per_window(m, hold)
                t = pd.DataFrame({"R": rr[take], "pct": pp[take]}, index=df.index[take])
                t = t[t["R"].notna()]
                rows.append(t.assign(rule=name, hold=hold, ticker=p.stem.upper()))
        print(p.stem, file=sys.stderr)
    res = pd.concat(rows)
    res.index.name = "date"
    res = res.reset_index().merge(mkt_above.reset_index().rename(columns={"Date": "date"}), on="date", how="left")
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_parquet(out)
    print(len(res))


if __name__ == "__main__":
    main()
