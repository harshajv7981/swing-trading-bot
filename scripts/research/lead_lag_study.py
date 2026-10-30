"""Big stocks down, others up next week? (docs/research/lead-lag-prereg.md)

    PYTHONPATH=scripts/research python scripts/research/lead_lag_study.py eod2_data/daily
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from weekly_rules_study import CASH_WEEK, MIN_HISTORY, MIN_VALUE, after_tax_cagr, cagr, equal, load, maxdd, sharpe

COST = 0.0030
LEADERS, TOP, WINDOW = 10, 20, 156


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x))


def slope(y, x):
    x, y = x - x.mean(), y - y.mean()
    b = (x * y).sum() / (x * x).sum()
    resid = y - b * x
    se = np.sqrt((resid ** 2).sum() / (len(x) - 2) / (x * x).sum())
    return b, b / se


def main():
    d = load(Path(sys.argv[1]))
    O, C, TV = d["O"], d["C"], d["TV"]
    days = C.index
    medval = TV.rolling(60, min_periods=40).median()
    keep = medval.columns[(medval >= MIN_VALUE).any()]
    O, C, medval = O[keep], C[keep], medval[keep]
    cf = C.ffill(limit=5)
    hist = C.notna().cumsum()
    week = pd.Series(days.to_period("W"), index=days)
    sig = np.flatnonzero((week != week.shift(-1)).to_numpy())
    wk_close = cf.iloc[sig]
    wk_ret = wk_close / wk_close.shift(1) - 1  # close-to-close return of the week ending at each signal day

    rows, held, groups = [], pd.Series(dtype=float), []
    model, model_year = {}, None
    for j in range(1, len(sig) - 1):
        s, e, x = sig[j], sig[j] + 1, sig[j + 1] + 1
        if x >= len(days) or days[s] < pd.Timestamp("2009-06-01"):
            continue
        ok = (hist.iloc[s] >= MIN_HISTORY) & (medval.iloc[s] >= MIN_VALUE) & C.iloc[s].notna() & O.iloc[e].notna()
        ranked = medval.iloc[s][ok].sort_values(ascending=False).index
        large, mid, small = ranked[:100], ranked[100:250], ranked[250:500]
        r = (O.iloc[x].fillna(cf.iloc[x]) / O.iloc[e] - 1).fillna(0.0)
        wr = wk_ret.iloc[j]
        groups.append((days[x], wr.reindex(large).mean(), r.reindex(mid).mean(), r.reindex(small).mean()))

        if days[s].year != model_year:  # refit each January on the previous 156 weeks only
            model_year = days[s].year
            past = wk_ret.iloc[max(1, j - WINDOW):j + 1]
            lead, follow = past[large].shift(1).iloc[1:], past.iloc[1:]
            lead = lead.loc[:, lead.notna().mean() > 0.9].fillna(0.0)
            model = {}
            for m in mid:
                y = follow[m]
                okk = y.notna()
                if okk.sum() < 100:
                    continue
                yy, xx = y[okk], lead[okk]
                xc, yc = xx - xx.mean(), yy - yy.mean()
                corr = (xc.mul(yc, axis=0)).sum() / np.sqrt((xc ** 2).sum() * (yc ** 2).sum())
                corr = corr.drop(m, errors="ignore").dropna()
                top = corr.abs().nlargest(LEADERS).index
                betas = (xc[top].mul(yc, axis=0)).sum() / (xc[top] ** 2).sum()
                model[m] = betas
        pred = pd.Series({m: float((b * wr.reindex(b.index).fillna(0.0)).sum()) for m, b in model.items() if m in mid})
        w = equal(pred.nlargest(TOP).index) if len(pred) >= TOP else pd.Series(dtype=float)
        ew = equal(mid)
        out = []
        for name, wt, prev in (("L2", w, held), ("EW", ew, None)):
            rr = r.reindex(wt.index).fillna(0.0)
            g = float((wt * rr).sum()) + (1 - wt.sum()) * CASH_WEEK
            if name == "L2":
                allk = wt.index.union(prev.index)
                traded = (wt.reindex(allk, fill_value=0) - prev.reindex(allk, fill_value=0)).abs().sum()
                held = wt * (1 + rr) / (1 + g)
                out.append(g - COST * traded)
            else:
                out.append(g - COST * 0.2)  # EW: about 10% of names change a week, as in the weekly study
        rows.append((days[x], *out))

    g = pd.DataFrame(groups, columns=["date", "large_this", "mid_next", "small_next"]).set_index("date")
    g = g[g.index >= "2010-01-01"]
    print("## L1: this week's Large return vs next week's group returns (open to open)")
    for name, sub in (("discovery 2010-19", g[g.index.year <= 2019]), ("holdout 2020-26", g[g.index.year >= 2020])):
        bm, tm = slope(sub["mid_next"], sub["large_this"])
        bs, ts = slope(sub["small_next"], sub["large_this"])
        print(f"{name}: Mid slope {bm:+.3f} (t {tm:+.1f}), Small slope {bs:+.3f} (t {ts:+.1f}), weeks {len(sub)}")
    for name, sub in (("discovery 2010-19", g[g.index.year <= 2019]), ("holdout 2020-26", g[g.index.year >= 2020])):
        after = sub.loc[sub["large_this"] < -0.02, "mid_next"]
        diff = after - sub["mid_next"].mean()
        print(f"{name}: after Large fell >2% ({len(after)} weeks) Mid next week {after.mean():+.2%} vs all weeks "
              f"{sub['mid_next'].mean():+.2%} (t {tstat(diff):+.1f}); share of those weeks Mid rose {(after > 0).mean():.0%}")
    big = g.loc[g["large_this"] < -0.05]
    print(f"After Large fell >5% ({len(big)} weeks, all years): Mid next week {big['mid_next'].mean():+.2%}, "
          f"rose in {(big['mid_next'] > 0).mean():.0%}")

    res = pd.DataFrame(rows, columns=["date", "L2", "EW"]).set_index("date")
    res = res[res.index >= "2010-01-01"]
    ex = res["L2"] - res["EW"]
    dsc, hold = ex[ex.index.year <= 2019], ex[ex.index.year >= 2020]
    yearly = ex.groupby(ex.index.year).apply(lambda q: (1 + q).prod() - 1)
    hy = yearly[yearly.index >= 2020]
    p = dsc.mean() > 0 and tstat(dsc) > 2 and hold.mean() > 0 and (hy > 0).sum() >= 4
    print("\n## L2: stock-to-stock lead-lag, top 20 Mid by predicted next-week return")
    for k in ("L2", "EW"):
        x = res[k]
        print(f"{k}: CAGR {cagr(x):+.1%}, after tax {after_tax_cagr(x):+.1%}, worst DD {maxdd(x):.1%}, Sharpe {sharpe(x):.2f}, "
              f"holdout after tax {after_tax_cagr(x[x.index.year >= 2020]):+.1%}")
    print(f"excess: discovery {dsc.mean():+.3%}/wk (t {tstat(dsc):+.1f}), holdout {hold.mean():+.3%}/wk "
          f"(t {tstat(hold):+.1f}), holdout years ahead {(hy > 0).sum()}/{len(hy)} -> {'PASS' if p else 'fail'}")
    print("yearly excess: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))


if __name__ == "__main__":
    main()
