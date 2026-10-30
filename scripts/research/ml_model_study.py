"""LightGBM stock ranker, walk-forward (docs/research/ml-model-prereg.md).

    PYTHONPATH=scripts/research python scripts/research/ml_model_study.py eod2_data/daily

Needs lightgbm. Prints results and the feature importances of the last model.
"""
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from weekly_rules_study import MIN_HISTORY, MIN_VALUE, after_tax_cagr, cagr, equal, load, maxdd, sharpe

PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=15, min_data_in_leaf=300, bagging_fraction=0.8,
              bagging_freq=1, feature_fraction=0.8, seed=0, verbose=-1)
ROUNDS = 300
TOP = 20
FIRST_TEST = 2012
STOCK_FEATS = ["r1w", "r1m", "r3m", "r6m", "r12m", "r6_1", "from_high", "from_sma200", "vol20", "vol60",
               "volratio", "logval", "rsi14"]


def tstat(x):
    return x.mean() / x.std() * np.sqrt(len(x))


def features(O, C, V, TV):
    cf = C.ffill(limit=5)
    ret = cf.pct_change(fill_method=None)
    delta = cf.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
    return cf, {
        "r1w": cf / cf.shift(5) - 1, "r1m": cf / cf.shift(21) - 1, "r3m": cf / cf.shift(63) - 1,
        "r6m": cf / cf.shift(126) - 1, "r12m": cf / cf.shift(252) - 1, "r6_1": cf.shift(21) / cf.shift(126) - 1,
        "from_high": cf / cf.rolling(252, min_periods=200).max() - 1,
        "from_sma200": cf / cf.rolling(200, min_periods=200).mean() - 1,
        "vol20": ret.rolling(20, min_periods=15).std(), "vol60": ret.rolling(60, min_periods=40).std(),
        "volratio": V.rolling(5, min_periods=3).mean() / V.rolling(50, min_periods=40).mean(),
        "logval": np.log(TV.rolling(60, min_periods=40).median()),
        "rsi14": 100 - 100 / (1 + up / dn),
    }


def panel(kind, days, sig, O, C, cf, hist, medval, feats):
    """Rows (date, sym, features, forward return) at each signal day; returns also the exit day of each period."""
    rows = []
    for j in range(len(sig) - 1):
        s = sig[j]
        if days[s] < pd.Timestamp("2009-06-01"):
            continue
        if kind == "W":
            e, x = s + 1, sig[j + 1] + 1
            if x >= len(days):
                break
            ok = (hist.iloc[s] >= MIN_HISTORY) & (medval.iloc[s] >= MIN_VALUE) & C.iloc[s].notna() & O.iloc[e].notna()
            names = medval.iloc[s][ok].sort_values(ascending=False).index[:250]
            fwd = O.iloc[x].fillna(cf.iloc[x]).reindex(names) / O.iloc[e].reindex(names) - 1
        else:
            x = sig[j + 1]
            ok = (hist.iloc[s] >= MIN_HISTORY) & (medval.iloc[s] >= MIN_VALUE) & cf.iloc[s].notna()
            names = medval.iloc[s][ok].sort_values(ascending=False).index[100:250]
            fwd = cf.iloc[x].reindex(names) / cf.iloc[s].reindex(names) - 1
        df = pd.DataFrame({k: v.iloc[s].reindex(names) for k, v in feats.items()})
        for k in STOCK_FEATS:
            df[k + "_pct"] = df[k].rank(pct=True)
        df["mkt1m"], df["mkt6m"] = df["r1m"].mean(), df["r6m"].mean()
        df["fwd"] = fwd.fillna(0.0)
        df["target"] = df["fwd"].rank(pct=True)
        df["date"], df["exit"] = days[s], days[x]
        rows.append(df.rename_axis("sym").reset_index())
    return pd.concat(rows, ignore_index=True)


def backtest(p, cost):
    """Top 20 by prediction vs EW, equal weight, drifting between rebalances; net returns by exit date."""
    out, held, held_ew = [], pd.Series(dtype=float), pd.Series(dtype=float)
    for d, g in p.groupby("date"):
        g = g.set_index("sym")
        r = g["fwd"]
        res = []
        for w, prev in ((equal(g["pred"].nlargest(TOP).index), held), (equal(g.index), held_ew)):
            allk = w.index.union(prev.index)
            traded = (w.reindex(allk, fill_value=0) - prev.reindex(allk, fill_value=0)).abs().sum()
            gross = float((w * r.reindex(w.index)).sum())
            res.append((gross - cost * traded, w * (1 + r.reindex(w.index)) / (1 + gross)))
        (rm, held), (re, held_ew) = res
        out.append((g["exit"].iloc[0], rm, re))
    return pd.DataFrame(out, columns=["date", "ml", "ew"]).set_index("date")


def walk_forward(p):
    cols = [c for c in p.columns if c not in ("sym", "date", "exit", "fwd", "target")]
    preds, model = [], None
    for y in range(FIRST_TEST, p["date"].dt.year.max() + 1):
        train = p[p["exit"] < pd.Timestamp(f"{y}-01-01")]  # labels fully known before the test year
        test = p[p["date"].dt.year == y]
        if test.empty:
            continue
        model = lgb.train(PARAMS, lgb.Dataset(train[cols], train["target"]), num_boost_round=ROUNDS)
        preds.append(test.assign(pred=model.predict(test[cols])))
    imp = pd.Series(model.feature_importance("gain"), index=cols)
    return pd.concat(preds), (imp / imp.sum()).sort_values(ascending=False)


def m6_monthly(p):
    """The paper trial's rule on the same monthly panel: top 20 by 6-1 month return."""
    return backtest(p.assign(pred=p["r6_1"]), 0.002)["ml"]


def report(name, res, per, m6=None):
    ex = res["ml"] - res["ew"]
    d, h = ex[ex.index.year <= 2019], ex[ex.index.year >= 2020]
    yr = ex.groupby(ex.index.year).apply(lambda q: (1 + q).prod() - 1)
    hy = yr[yr.index >= 2020]
    ok = d.mean() > 0 and tstat(d) > 2 and h.mean() > 0 and (hy > 0).sum() >= 4
    print(f"\n### {name}")
    for k, x in (("model", res["ml"]), ("EW", res["ew"])) + ((("M6", m6),) if m6 is not None else ()):
        xh = x[x.index.year >= 2020]
        print(f"{k}: CAGR {cagr(x, per):+.1%}, after tax {after_tax_cagr(x, per):+.1%}, worst DD {maxdd(x):.1%}, "
              f"Sharpe {sharpe(x, per):.2f}; holdout CAGR {cagr(xh, per):+.1%}, after tax {after_tax_cagr(xh, per):+.1%}")
    print(f"vs EW: discovery {d.mean():+.3%} (t {tstat(d):+.1f}), holdout {h.mean():+.3%} (t {tstat(h):+.1f}), "
          f"years ahead {(hy > 0).sum()}/{len(hy)} -> {'pass' if ok else 'fail'}")
    print("yearly excess vs EW: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yr.items()))
    if m6 is not None:
        e2 = (res["ml"] - m6).dropna()
        d2, h2 = e2[e2.index.year <= 2019], e2[e2.index.year >= 2020]
        beats = (d2.mean() > 0 and h2.mean() > 0
                 and after_tax_cagr(res["ml"][res.index.year >= 2020], per) > after_tax_cagr(m6[m6.index.year >= 2020], per))
        print(f"vs M6: discovery {d2.mean():+.3%} (t {tstat(d2):+.1f}), holdout {h2.mean():+.3%} (t {tstat(h2):+.1f}) "
              f"-> {'beats M6' if beats else 'does not beat M6'}; overall {'PASS' if ok and beats else 'FAIL'}")


def main():
    d = load(Path(sys.argv[1]))
    O, C, V, TV = (d[k] for k in ("O", "C", "V", "TV"))
    days = C.index
    medval = TV.rolling(60, min_periods=40).median()
    keep = medval.columns[(medval >= MIN_VALUE).any()]
    O, C, V, TV, medval = (f[keep] for f in (O, C, V, TV, medval))
    hist = C.notna().cumsum()
    cf, feats = features(O, C, V, TV)
    week = pd.Series(days.to_period("W"), index=days)
    sig_w = np.flatnonzero((week != week.shift(-1)).to_numpy())
    month = pd.Series(days.to_period("M"), index=days)
    sig_m = np.flatnonzero((month != month.shift(-1)).to_numpy())

    pm = panel("M", days, sig_m, O, C, cf, hist, medval, feats)
    predm, impm = walk_forward(pm)
    resm = backtest(predm, 0.002)
    report("ML-M (monthly, Mid, top 20)", resm, 12, m6_monthly(predm))
    print("feature importance (last model): " + ", ".join(f"{k} {v:.0%}" for k, v in impm.head(8).items()))

    pw = panel("W", days, sig_w, O, C, cf, hist, medval, feats)
    predw, impw = walk_forward(pw)
    resw = backtest(predw, 0.0025)
    report("ML-W (weekly, Liquid 250, top 20)", resw, 52)
    print("feature importance (last model): " + ", ".join(f"{k} {v:.0%}" for k, v in impw.head(8).items()))

    last = predm[predm["date"] == predm["date"].max()].nlargest(TOP, "pred")["sym"].tolist()
    print(f"\nML-M picks at {predm['date'].max().date()}: {', '.join(last)}")


if __name__ == "__main__":
    main()
