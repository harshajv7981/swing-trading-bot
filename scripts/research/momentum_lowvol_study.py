"""Mid-cap momentum plus low volatility (docs/research/momentum-lowvol-prereg.md).

    python scripts/research/momentum_lowvol_study.py eod2_data/daily

Arms on the mid bucket (ranks 101-250 by traded value, rebuilt monthly), all
rebalanced monthly with drifting weights: A (M6 top 20), Blend (half A, half
the 20 lowest-volatility stocks), Calm (M6 top 20 within the calmer half) and
L (lowest volatility alone, information only).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot.momentum import load

LO, HI = 100, 250
TOP = 20
COST = 0.0040  # per unit of one-way turnover, as in momentum_broad_study.py
MIN_VALUE = 5e7  # Rs 5 crore
VOL_DAYS, VOL_MIN = 252, 200
CASH_YEAR = 0.06
STCG, LTCG, FUND_FEE = 0.20, 0.125, 0.004
INDEX_FILE = "nifty midcap150 momentum 50.csv"
ARMS = ("A", "Blend", "Calm", "L (info only)")
JUDGED = ("Blend", "Calm")


def cagr(x):
    return (1 + x).prod() ** (12 / len(x)) - 1


def maxdd(x):
    eq = (1 + x).cumprod()
    return (eq / eq.cummax() - 1).min()


def sharpe(x):
    ex = x - ((1 + CASH_YEAR) ** (1 / 12) - 1)
    return ex.mean() / ex.std() * np.sqrt(12)


def after_tax_stcg(x):
    """CAGR after 20% tax each calendar year on net gains, losses carried forward."""
    w, carry = 1.0, 0.0
    for _, s in x.groupby(x.index.year):
        start = w
        w *= (1 + s).prod()
        gain = w - start
        if gain < 0:
            carry -= gain
        else:
            used = min(carry, gain)
            carry -= used
            w -= STCG * (gain - used)
    return w ** (12 / len(x)) - 1


def after_tax_fund(x):
    """CAGR of an index fund: annual fee, then 12.5% on the whole gain at sale."""
    w = ((1 + x) * (1 - FUND_FEE / 12)).prod()
    w -= LTCG * max(w - 1, 0)
    return w ** (12 / len(x)) - 1


def equal(names):
    return pd.Series(1 / len(names), index=names) if len(names) else pd.Series(dtype=float)


def main():
    data = Path(sys.argv[1])
    px, val = load(data)
    print(f"{px.shape[1]} stocks loaded", file=sys.stderr)
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[me_dates]
    medval = val.rolling(60, min_periods=40).median().loc[me_dates]
    vol = px.pct_change(fill_method=None).rolling(VOL_DAYS, min_periods=VOL_MIN).std().loc[me_dates]

    ew_prev = pd.Series(dtype=float)
    held = {k: pd.Series(dtype=float) for k in ARMS}
    rows = {k: [] for k in ARMS}
    ew_rows = []
    for i in range(len(me_dates) - 1):
        a, nxt = me_dates[i], me_dates[i + 1]
        ok = (hist.loc[a] >= 260) & (medval.loc[a] >= MIN_VALUE) & me_px.loc[a].notna()
        names = medval.loc[a][ok].sort_values(ascending=False).index[LO:HI]
        # No price a month later (suspended): counted flat, as before.
        r = (me_px.loc[nxt] / me_px.loc[a] - 1).fillna(0.0)

        ew = equal(names)
        allk = ew.index.union(ew_prev.index)
        turn = 0.5 * (ew.reindex(allk, fill_value=0) - ew_prev.reindex(allk, fill_value=0)).abs().sum()
        ew_rows.append((nxt, float((ew * r.reindex(ew.index)).sum()) - COST * turn))
        ew_prev = ew

        if i < 6:
            continue
        score = (me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(names).dropna()
        v = vol.loc[a].reindex(names).dropna()
        mom = score.nlargest(TOP).index
        calm = v.nsmallest(TOP).index
        calmer_half = v[v <= v.median()].index
        targets = {
            "A": equal(mom),
            "Blend": 0.5 * equal(mom).add(equal(calm), fill_value=0),
            "Calm": equal(score.reindex(calmer_half).dropna().nlargest(TOP).index),
            "L (info only)": equal(calm),
        }
        for k, target in targets.items():
            cur = held[k]
            allk = target.index.union(cur.index)
            turn = 0.5 * (target.reindex(allk, fill_value=0) - cur.reindex(allk, fill_value=0)).abs().sum()
            rr = r.reindex(target.index).fillna(0.0)
            gross = float((target * rr).sum())
            rows[k].append((nxt, gross - COST * turn, turn))
            held[k] = target * (1 + rr) / (1 + gross)

    def window(df):
        return df[(df.index >= "2010-01-01") & (df.index < "2026-10-01")]

    ew_s = window(pd.DataFrame(ew_rows, columns=["date", "ret"]).set_index("date"))["ret"]
    res = {k: window(pd.DataFrame(v, columns=["date", "ret", "turn"]).set_index("date")) for k, v in rows.items()}

    idx = pd.read_csv(data / INDEX_FILE, usecols=["Date", "Close"], parse_dates=["Date"],
                      index_col="Date")["Close"].sort_index()
    idx = idx[~idx.index.duplicated()]
    idx_me = idx.reindex(idx.index.union(me_dates)).ffill(limit=5).loc[me_dates]
    idx_ret = window((idx_me / idx_me.shift() - 1).dropna())  # dated by the month-end it ends on
    span = f"{idx_ret.index[0]:%b %Y} - {idx_ret.index[-1]:%b %Y}"
    fund_bar = after_tax_fund(idx_ret)

    print(f"Mid-bucket EW: CAGR {cagr(ew_s):+.1%}, max dd {maxdd(ew_s):.1%}, Sharpe {sharpe(ew_s):.2f}")
    print(f"Momentum 50 index ({span}, {len(idx_ret)} months): price CAGR {cagr(idx_ret):+.1%}, "
          f"max dd {maxdd(idx_ret):.1%}, as a fund after fee and 12.5% LTCG {fund_bar:+.1%}")
    base_dd = maxdd(res["A"]["ret"])
    for k, df in res.items():
        x = df["ret"]
        ex = x - ew_s
        disc, hold = ex[ex.index.year <= 2019], ex[ex.index.year >= 2020]
        t_d = disc.mean() / disc.std() * np.sqrt(len(disc))
        t_h = hold.mean() / hold.std() * np.sqrt(len(hold))
        yearly_ex = ex.groupby(ex.index.year).apply(lambda s: (1 + s).prod() - 1)
        hold_years = yearly_ex[yearly_ex.index >= 2020]
        dd = maxdd(x)
        print(f"\n######## {k}")
        print(f"CAGR {cagr(x):+.1%}, max dd {dd:.1%}, Sharpe {sharpe(x):.2f}, turnover {df['turn'].mean():.0%}/month")
        print(f"  discovery: excess {disc.mean():+.2%}/month (t {t_d:+.1f}, n {len(disc)})")
        print(f"  holdout: excess {hold.mean():+.2%}/month (t {t_h:+.1f}, n {len(hold)}), "
              f"years beating EW {(hold_years > 0).sum()}/{len(hold_years)}")
        sub = after_tax_stcg(x.reindex(idx_ret.index))
        print(f"  after 20% STCG: {after_tax_stcg(x):+.1%} (full period); {sub:+.1%} over {span} "
              f"vs fund {fund_bar:+.1%}")
        yearly = x.groupby(x.index.year).apply(lambda s: (1 + s).prod() - 1)
        print("  yearly: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))
        if k in JUDGED:
            c1 = disc.mean() > 0 and t_d > 2 and hold.mean() > 0 and (hold_years > 0).sum() >= 4
            c2 = dd >= base_dd * (2 / 3)
            print(f"  VERDICT: beats EW {'pass' if c1 else 'fail'}, drawdown {'pass' if c2 else 'fail'} "
                  f"(needs {base_dd * 2 / 3:.1%} or better) -> {'PASS' if c1 and c2 else 'FAIL'}")
    ye = ew_s.groupby(ew_s.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("\nMid EW yearly: " + ", ".join(f"{y}:{v:+.0%}" for y, v in ye.items()))


if __name__ == "__main__":
    main()
