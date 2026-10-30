"""Kronos weekly stock picks, Sep 2025 to Oct 2026 (docs/research/kronos-prereg.md).

    PYTHONPATH=scripts/research python scripts/research/kronos_study.py eod2_data/daily KRONOS_REPO WEIGHTS preds.csv

KRONOS_REPO is a clone of github.com/shiyu-coder/Kronos. WEIGHTS holds small/ and tok/,
each with config.json and model.safetensors from NeoQuasar/Kronos-small and
NeoQuasar/Kronos-Tokenizer-base. Forecasts are appended to preds.csv week by week,
so a stopped run resumes where it left off. Needs torch, einops, safetensors.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ml_model_study import backtest, features, m6_monthly, panel, tstat
from weekly_rules_study import MIN_HISTORY, MIN_VALUE, cagr, load, maxdd

START = pd.Timestamp("2025-09-01")
LOOKBACK, PRED_LEN, BATCH = 400, 5, 50
COLS = ["open", "high", "low", "close", "volume", "amount"]


def predictor(repo, weights):
    import torch
    sys.path.insert(0, str(repo))
    from model import Kronos, KronosPredictor, KronosTokenizer
    tok = KronosTokenizer.from_pretrained(str(Path(weights) / "tok"))
    model = Kronos.from_pretrained(str(Path(weights) / "small"))
    tok.eval(), model.eval()
    torch.set_grad_enabled(False)
    return KronosPredictor(model, tok, device="cpu", max_context=512)


def forecast(pred, d, s, names):
    """Kronos score (forecast close on day 5 / last close - 1) for each name, data to day s only."""
    import torch
    torch.manual_seed(0)
    days = d["C"].index
    future = pd.Series(pd.bdate_range(days[s] + pd.offsets.BDay(1), periods=PRED_LEN))
    groups = {}
    for n in names:
        df = pd.DataFrame({c: d[k][n].iloc[max(0, s - 2 * LOOKBACK):s + 1]
                           for c, k in zip(COLS, ("O", "H", "L", "C", "V", "TV"))}).dropna().iloc[-LOOKBACK:]
        groups.setdefault(len(df), []).append((n, df))
    out = {}
    for items in groups.values():
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            res = pred.predict_batch([df.reset_index(drop=True) for _, df in chunk],
                                     [pd.Series(df.index) for _, df in chunk], [future] * len(chunk),
                                     pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=1, verbose=False)
            for (n, df), r in zip(chunk, res):
                out[n] = float(r["close"].iloc[-1] / df["close"].iloc[-1] - 1)
    return pd.Series(out)


def main():
    data, repo, weights, cache = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
    d = load(Path(data))
    O, C, V, TV = (d[k] for k in ("O", "C", "V", "TV"))
    days = C.index
    medval = TV.rolling(60, min_periods=40).median()
    keep = medval.columns[(medval >= MIN_VALUE).any()]
    d = {k: v[keep] for k, v in d.items()}
    O, C, V, TV, medval = (f[keep] for f in (O, C, V, TV, medval))
    hist = C.notna().cumsum()
    cf = C.ffill(limit=5)
    week = pd.Series(days.to_period("W"), index=days)
    sig = np.flatnonzero((week != week.shift(-1)).to_numpy())

    done = pd.read_csv(cache, parse_dates=["date", "exit"]) if cache.exists() else pd.DataFrame()
    pred = None
    for j in range(len(sig) - 1):
        s = sig[j]
        e, x = s + 1, sig[j + 1] + 1
        if days[s] < START or x >= len(days):
            continue
        if not done.empty and (done["date"] == days[s]).any():
            continue
        ok = (hist.iloc[s] >= MIN_HISTORY) & (medval.iloc[s] >= MIN_VALUE) & C.iloc[s].notna() & O.iloc[e].notna()
        names = medval.iloc[s][ok].sort_values(ascending=False).index[:250]
        pred = pred or predictor(repo, weights)
        score = forecast(pred, d, s, names)
        fwd = (O.iloc[x].fillna(cf.iloc[x]).reindex(names) / O.iloc[e].reindex(names) - 1).fillna(0.0)
        week_df = pd.DataFrame({"sym": names, "pred": score.reindex(names).to_numpy(), "fwd": fwd.to_numpy(),
                                "date": days[s], "exit": days[x]})
        week_df.to_csv(cache, mode="a", header=not cache.exists(), index=False)
        done = pd.concat([done, week_df])
        print(f"{days[s].date()}: {len(names)} stocks", flush=True)

    p = done.dropna(subset=["pred"])
    ic = p.groupby("date").apply(lambda g: g["pred"].rank().corr(g["fwd"].rank()))
    res = backtest(p, 0.0025)
    ex = res["ml"] - res["ew"]

    _, feats = features(O, C, V, TV)
    pm = panel("M", days, np.flatnonzero((pd.Series(days.to_period("M"), index=days)
                                          != pd.Series(days.to_period("M"), index=days).shift(-1)).to_numpy()),
               O, C, cf, hist, medval, feats)
    pm = pm[pm["date"] >= START - pd.offsets.MonthEnd(1)]
    m6 = m6_monthly(pm)
    end = min(res.index.max(), m6.index.max())
    kw_same, m6_same = res["ml"][res.index <= end], m6[m6.index <= end]

    print(f"\n{len(res)} weeks, signals {p['date'].min().date()} to {p['date'].max().date()}, {p['sym'].nunique()} stocks")
    print(f"1. rank correlation: mean {ic.mean():+.3f}, t {tstat(ic):+.1f}, positive in {(ic > 0).mean():.0%} of weeks"
          f" -> {'pass' if ic.mean() > 0 and tstat(ic) > 2 else 'fail'}")
    for k, r in (("Kronos top 20", res["ml"]), ("Liquid 250 EW", res["ew"])):
        print(f"   {k}: growth {(1 + r).prod() - 1:+.1%}, CAGR {cagr(r, 52):+.1%}, worst DD {maxdd(r):.1%}")
    print(f"2. weekly excess over EW: {ex.mean():+.3%} (t {tstat(ex):+.1f}), ahead in {(ex > 0).mean():.0%} of weeks"
          f" -> {'pass' if ex.mean() > 0 else 'fail'}")
    kg, mg = (1 + kw_same).prod() - 1, (1 + m6_same).prod() - 1
    print(f"3. to {end.date()}: Kronos {kg:+.1%} vs monthly mid M6 {mg:+.1%} ({len(m6_same)} months, "
          f"DD {maxdd(m6_same):.1%}) -> {'pass' if kg > mg else 'fail'}")
    print("monthly Kronos minus EW: " + ", ".join(
        f"{m}:{v:+.1%}" for m, v in ex.groupby(ex.index.to_period("M")).apply(lambda q: (1 + q).prod() - 1).items()))
    last = p[p["date"] == p["date"].max()].nlargest(20, "pred")["sym"].tolist()
    print(f"last picks ({p['date'].max().date()}): {', '.join(last)}")


if __name__ == "__main__":
    main()
