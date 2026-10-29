"""Tables for the pre-registered price-rule study: discovery 2010-19, holdout 2020-26.

    python scripts/research/price_rules_tables.py runs/research/price_rules.parquet
"""
import sys

import numpy as np
import pandas as pd

RULES = ["A_thrust", "B_pullback", "C_52w_high", "D_shock_dip"]


def period(d):
    return np.where(d.dt.year <= 2019, "discovery", "holdout")


def main():
    t = pd.read_parquet(sys.argv[1])
    t["date"] = pd.to_datetime(t["date"])
    t["period"] = period(t["date"])
    t["year"] = t["date"].dt.year
    for hold in (5, 10):
        h = t[t["hold"] == hold]
        print(f"\n=== hold {hold} sessions ===")
        base = h[h["rule"] == "TREND"].groupby("period")["R"].agg(["mean", "std", "count"])
        base_y = h[h["rule"] == "TREND"].groupby("year")["R"].mean()
        print("TREND", base.round(3).to_dict("index"))
        print("ALL  ", h[h["rule"] == "ALL"].groupby("period")["R"].mean().round(3).to_dict())
        for r in RULES:
            x = h[h["rule"] == r]
            line = [r]
            for per in ("discovery", "holdout"):
                y = x[x["period"] == per]["R"]
                b = base.loc[per]
                diff = y.mean() - b["mean"]
                se = np.sqrt(y.var() / len(y) + b["std"] ** 2 / b["count"])
                win = (y > 0).mean()
                pct = x[x["period"] == per]["pct"].mean() * 100
                line.append(f"{per}: n={len(y)} R={y.mean():+.3f} vs TREND {diff:+.3f} (t={diff / se:+.1f}) "
                            f"win={win:.0%} pct={pct:+.2f}%")
            yrs = x.groupby("year")["R"].mean()
            hy = [yy for yy in yrs.index if yy >= 2020]
            beat = sum(yrs[yy] > base_y[yy] for yy in hy)
            line.append(f"holdout years beating TREND: {beat}/{len(hy)}")
            print("\n  ".join(line))


if __name__ == "__main__":
    main()
