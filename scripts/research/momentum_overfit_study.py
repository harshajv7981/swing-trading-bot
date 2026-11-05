"""Is mid-cap M6 overfit? (docs/research/momentum-overfit-prereg.md)

    python scripts/research/momentum_overfit_study.py eod2_data/daily

72 neighbours of M6 (lookback x skip x stocks held x bucket), each judged on its
monthly excess over the same bucket's equal weight, then: the plateau, the
probability of backtest overfitting by CSCV, the deflated Sharpe ratio of M6,
and a yearly walk-forward choice among the 72. The universe, ranking and costs
are the broad study's (momentum_broad_study.py).
"""
import math
import sys
from itertools import combinations, pairwise, product
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

from swingbot.momentum import load

LOOKBACKS, SKIPS, TOPS = (3, 6, 9, 12), (0, 1), (10, 20, 30)
BUCKETS = {"76-225": (75, 225), "101-250": (100, 250), "126-275": (125, 275)}
M6 = (6, 1, 20, "101-250")
COST = 0.0040  # round trip, mid caps
MIN_VALUE = 5e7
START, END = "2010-01-01", "2026-10-01"
BLOCKS = 16
PRIOR_TRIALS = 15
WF_FROM, WF_TRAIN = 2015, 60


def excess_returns(px: pd.DataFrame, val: pd.DataFrame) -> pd.DataFrame:
    """Monthly excess over the bucket's EW, after costs: one column per grid rule."""
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]
    rules = list(product(LOOKBACKS, SKIPS, TOPS, BUCKETS))
    prev = {k: pd.Series(dtype=float) for k in rules + [("EW", b) for b in BUCKETS]}
    rows, dates = [], []

    def held(key, names, r):
        w = pd.Series(1 / len(names), index=names) if len(names) else pd.Series(dtype=float)
        rr = r.reindex(w.index).fillna(0.0)  # no price a month later (suspended): counted flat
        allk = w.index.union(prev[key].index)
        turn = 0.5 * (w.reindex(allk, fill_value=0) - prev[key].reindex(allk, fill_value=0)).abs().sum()
        prev[key] = w
        return float((w * rr).sum()) - COST * turn

    for i, (a, nxt) in enumerate(pairwise(me_dates)):
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        ranked = medval.loc[a][ok].sort_values(ascending=False).index
        r = me_px.loc[nxt] / me_px.loc[a] - 1
        ew = {b: held(("EW", b), ranked[lo:hi], r) for b, (lo, hi) in BUCKETS.items()}
        row = []
        for lb, skip, top, b in rules:
            lo, hi = BUCKETS[b]
            if i < lb:
                row.append(math.nan)
                continue
            score = (me_px.iloc[i - skip] / me_px.iloc[i - lb] - 1).reindex(ranked[lo:hi]).dropna()
            row.append(held((lb, skip, top, b), score.nlargest(top).index, r) - ew[b])
        rows.append(row)
        dates.append(nxt)
    ex = pd.DataFrame(rows, index=pd.DatetimeIndex(dates), columns=pd.MultiIndex.from_tuples(rules))
    return ex[(ex.index >= START) & (ex.index < END)]


def tstat(x: pd.Series) -> float:
    return x.mean() / x.std() * math.sqrt(len(x))


def pbo(ex: pd.DataFrame, blocks: int = BLOCKS) -> tuple[float, float]:
    """CSCV: share of half/half block splits where the in-sample best lands below the OOS median.

    Also returns the median OOS rank (0 to 1) of the in-sample best."""
    x = ex.to_numpy()
    edges = np.linspace(0, len(x), blocks + 1).astype(int)
    s = np.array([x[a:b].sum(0) for a, b in pairwise(edges)])
    q = np.array([(x[a:b] ** 2).sum(0) for a, b in pairwise(edges)])
    n = np.diff(edges)
    tot_s, tot_q, tot_n = s.sum(0), q.sum(0), n.sum()

    def sharpe(ss, qq, nn):
        m = ss / nn
        return m / np.sqrt(qq / nn - m ** 2)

    below, ranks = 0, []
    for ins in combinations(range(blocks), blocks // 2):
        ins = list(ins)
        si, qi, ni = s[ins].sum(0), q[ins].sum(0), n[ins].sum()
        best = int(np.argmax(sharpe(si, qi, ni)))
        oos = sharpe(tot_s - si, tot_q - qi, tot_n - ni)
        rank = (oos < oos[best]).sum() / (len(oos) - 1)
        ranks.append(rank)
        below += rank < 0.5
    return below / len(ranks), float(np.median(ranks))


def deflated_sharpe(x: pd.Series, trial_srs: pd.Series, trials: int) -> tuple[float, float, float]:
    """Bailey and Lopez de Prado (2014), per-period Sharpe. Returns (DSR, SR, SR0)."""
    nd, g = NormalDist(), 0.5772156649
    sr = x.mean() / x.std()
    z = (x - x.mean()) / x.std(ddof=0)
    skew, kurt = (z ** 3).mean(), (z ** 4).mean()
    sr0 = trial_srs.std() * ((1 - g) * nd.inv_cdf(1 - 1 / trials) + g * nd.inv_cdf(1 - 1 / (trials * math.e)))
    dsr = nd.cdf((sr - sr0) * math.sqrt(len(x) - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return dsr, sr, sr0


def walk_forward(ex: pd.DataFrame) -> tuple[pd.Series, list[tuple[int, tuple]]]:
    chain, picks = [], []
    for y in range(WF_FROM, ex.index.year.max() + 1):
        train = ex[ex.index.year < y].tail(WF_TRAIN)
        best = train.mean().idxmax()
        picks.append((y, best))
        chain.append(ex.loc[ex.index.year == y, best])
    return pd.concat(chain), picks


def name(rule) -> str:
    lb, skip, top, b = rule
    return f"L{lb} skip{skip} top{top} ranks {b}"


def main():
    px, val = load(Path(sys.argv[1]))
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    ex = excess_returns(px, val)
    m6 = ex[M6]
    print(f"Months {ex.index[0]:%Y-%m} to {ex.index[-1]:%Y-%m} ({len(ex)}), {ex.shape[1]} rules\n")
    for per, e in (("discovery 2010-19", m6[m6.index.year <= 2019]), ("holdout 2020-26", m6[m6.index.year >= 2020])):
        print(f"M6 check against the broad study: {per} {e.mean():+.2%}/month (t {tstat(e):+.1f})")

    means = ex.mean()
    pos, med = (means > 0).mean(), means.median()
    t1 = pos >= 0.75 and med >= 0.5 * means[M6]
    print(f"\n1. PLATEAU: {pos:.0%} of rules have positive excess (need 75%); median {med:+.2%}/month, "
          f"M6 {means[M6]:+.2%} (need half: {0.5 * means[M6]:+.2%}) -> {'PASS' if t1 else 'FAIL'}")
    print("   M6's rank among the 72 by mean excess: "
          f"{int((means > means[M6]).sum()) + 1}")
    for lvl, label in ((0, "lookback"), (1, "skip"), (2, "stocks held"), (3, "bucket")):
        g = means.groupby(level=lvl)
        print(f"   by {label}: " + ", ".join(f"{k}: {v:+.2%} ({(means.xs(k, level=lvl) > 0).mean():.0%} > 0)"
                                         for k, v in g.median().items()))
    print("   top 5: " + "; ".join(f"{name(k)} {v:+.2%}" for k, v in means.nlargest(5).items()))
    print("   bottom 5: " + "; ".join(f"{name(k)} {v:+.2%}" for k, v in means.nsmallest(5).items()))

    p, mrank = pbo(ex)
    print(f"\n2. PBO (CSCV, {BLOCKS} blocks): {p:.2f} (need below 0.50); median OOS rank of the "
          f"in-sample best {mrank:.2f} -> {'PASS' if p < 0.5 else 'FAIL'}")

    srs = ex.mean() / ex.std()
    trials = ex.shape[1] + PRIOR_TRIALS
    dsr, sr, sr0 = deflated_sharpe(m6, srs, trials)
    print(f"\n3. DSR: M6 monthly Sharpe of excess {sr:.3f} ({sr * math.sqrt(12):.2f} a year), expected best of "
          f"{trials} by luck {sr0:.3f}; DSR {dsr:.2f} (need 0.95) -> {'PASS' if dsr >= 0.95 else 'FAIL'}")
    t3 = dsr >= 0.95

    wf, picks = walk_forward(ex)
    same = m6.loc[wf.index]
    print(f"\n4. WALK-FORWARD (best mean excess over the previous {WF_TRAIN} months, chosen each January):")
    print(f"   chain {wf.mean():+.2%}/month (t {tstat(wf):+.1f}) vs M6 held throughout {same.mean():+.2%} "
          f"(t {tstat(same):+.1f}), {len(wf)} months")
    print("   picks: " + "; ".join(f"{y}: {name(r)}" for y, r in picks))

    t2 = p < 0.5
    verdict = ("ROBUST" if t1 and t2 and t3 else "REAL BUT OVERSTATED" if t1 and t2 else "FRAGILE")
    print(f"\nVERDICT: {verdict} (plateau {'pass' if t1 else 'fail'}, PBO {'pass' if t2 else 'fail'}, "
          f"DSR {'pass' if t3 else 'fail'})")


if __name__ == "__main__":
    main()
