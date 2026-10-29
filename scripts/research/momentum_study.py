"""Pre-registered multi-month momentum test (docs/research/momentum-prereg.md).

    python scripts/research/momentum_study.py eod2_data/daily

Month-end rebalance; EW baseline, M12 (12-1 month), M6 (6-1 month) and M12F
(M12, in cash when NIFTYBEES starts the month under its 200-day average).
Cost: 0.25% times the fraction of the portfolio replaced.
"""
import sys
from itertools import pairwise
from pathlib import Path

import numpy as np
import pandas as pd
from momentum_broad_study import adjust_jumps

COST = 0.0025
TOP = 10


UNIVERSE = Path(__file__).resolve().parents[2] / "data/universe/nifty100.csv"


def closes(data):
    wanted = {"niftybees"} | {line.split(",")[0].lower().replace("&", "_")
                              for line in UNIVERSE.read_text().splitlines()[1:] if line}
    out = {}
    for p in sorted(x for x in data.glob("*.csv") if x.stem in wanted):
        df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
        if "Series" in df:
            df = df[df["Series"].fillna("EQ") == "EQ"]
        s = df["Close"].astype(float)
        out[p.stem.upper()] = adjust_jumps(s[~s.index.duplicated()].sort_index())
    return pd.DataFrame(out)


def run(px, weights_fn, cash_mask=None):
    month_end = px.groupby(px.index.to_period("M")).tail(1)
    sessions = px.notna().cumsum()
    rets, prev_w = [], pd.Series(dtype=float)
    dates = month_end.index
    for a, b in pairwise(dates):
        w = weights_fn(a, month_end, sessions.loc[a])
        if cash_mask is not None and cash_mask.loc[a]:
            w = pd.Series(dtype=float)
        r = (month_end.loc[b] / month_end.loc[a] - 1).reindex(w.index).fillna(0.0)
        allk = w.index.union(prev_w.index)
        turn = 0.5 * (w.reindex(allk, fill_value=0) - prev_w.reindex(allk, fill_value=0)).abs().sum()
        rets.append((b, float((w * r).sum()) - COST * turn, turn))
        prev_w = w
    return pd.DataFrame(rets, columns=["date", "ret", "turn"]).set_index("date")


def eligible(row_sessions, prices_now):
    return row_sessions[(row_sessions >= 260) & prices_now.notna()].index


def ew(a, me, sess):
    names = eligible(sess.drop("NIFTYBEES", errors="ignore"), me.loc[a])
    return pd.Series(1 / len(names), index=names) if len(names) else pd.Series(dtype=float)


def mom(months):
    def fn(a, me, sess):
        i = me.index.get_loc(a)
        if i < months:
            return pd.Series(dtype=float)
        names = eligible(sess.drop("NIFTYBEES", errors="ignore"), me.loc[a])
        score = (me.iloc[i - 1] / me.iloc[i - months] - 1).reindex(names).dropna()
        top = score.nlargest(TOP).index
        return pd.Series(1 / len(top), index=top) if len(top) else pd.Series(dtype=float)
    return fn


def stats(x):
    n = len(x)
    m, s = x.mean(), x.std()
    return m, (m / s * np.sqrt(n) if s > 0 else np.nan), n


def cagr(r):
    years = len(r) / 12
    return (1 + r).prod() ** (1 / years) - 1


def maxdd(r):
    eq = (1 + r).cumprod()
    return (eq / eq.cummax() - 1).min()


def main():
    px = closes(Path(sys.argv[1]))
    px = px[px.index >= "2008-01-01"]
    nifty = px["NIFTYBEES"]
    below = (nifty < nifty.rolling(200).mean())
    stocks = px.drop(columns="NIFTYBEES")
    me_idx = stocks.groupby(stocks.index.to_period("M")).tail(1).index
    cash = below.reindex(me_idx)
    res = {"EW": run(stocks, ew), "M12": run(stocks, mom(12)), "M6": run(stocks, mom(6)),
           "M12F": run(stocks, mom(12), cash)}
    full = {k: v[(v.index >= "2010-01-01") & (v.index < "2026-10-01")] for k, v in res.items()}
    last_full = full["EW"].index.max()
    print(f"months {full['EW'].index.min().date()} to {last_full.date()}")
    base = full["EW"]["ret"]
    for name in ("EW", "M12", "M6", "M12F"):
        r = full[name]["ret"]
        print(f"\n{name}: CAGR {cagr(r):+.1%}, max drawdown {maxdd(r):.1%}, "
              f"mean turnover {full[name]['turn'].mean():.0%}/month")
        if name == "EW":
            for per, rr in (("2010-19", r[r.index.year <= 2019]), ("2020-26", r[r.index.year >= 2020])):
                print(f"  {per}: CAGR {cagr(rr):+.1%}, max dd {maxdd(rr):.1%}")
            continue
        ex = r - base
        for per, e, rr in (("discovery 2010-19", ex[ex.index.year <= 2019], r[r.index.year <= 2019]),
                           ("holdout 2020-26", ex[ex.index.year >= 2020], r[r.index.year >= 2020])):
            m, t, n = stats(e)
            print(f"  {per}: excess {m:+.2%}/month (t {t:+.1f}, n {n}), CAGR {cagr(rr):+.1%}, "
                  f"max dd {maxdd(rr):.1%}")
        yearly = ex.groupby(ex.index.year).apply(lambda s: (1 + s).prod() - 1)
        hy = yearly[yearly.index >= 2020]
        print("  yearly excess: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))
        print(f"  holdout years beating EW: {(hy > 0).sum()}/{len(hy)}")


if __name__ == "__main__":
    main()
