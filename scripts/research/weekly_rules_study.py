"""Weekly stock-pick rules (docs/research/weekly-rules-prereg.md).

    python scripts/research/weekly_rules_study.py eod2_data/daily

Five weekly rules, each judged against equal weight in its own universe:
signal at the week's last close, trade at the next open, hold to the open after
the next signal day. Costs per side, STCG each calendar year. Self-contained (no
swingbot import) so it runs on a bare Python with pandas.
"""
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

NOT_STOCK = re.compile(r"\s|etf|bees$|sensex|nifty|liquid|gilt|gsec")  # as swingbot.momentum
MIN_HISTORY = 260
MIN_VALUE = 5e7  # Rs 5 crore
TOP = 20
COST = {"U": 0.0025, "Mid": 0.0030}  # per side
CASH_YEAR = 0.06
CASH_WEEK = (1 + CASH_YEAR) ** (1 / 52) - 1
STCG, LTCG = 0.208, 0.13
RULES = {  # name: universe
    "W1 Reversal": "U",
    "W2 Short momentum": "U",
    "W3 52-week high": "U",
    "W4 Mid M6 weekly": "Mid",
    "W5 Breakout": "U",
    "W6 Trend template": "U",
    "W7 RSI(2) pullback": "U",
}
START, SPLIT = "2010-01-01", 2020


def jump_factor(c):
    """Factor that back-adjusts one-day falls over 40% (swingbot.momentum.adjust_jumps)."""
    ratio = c / c.shift()
    factor = pd.Series(1.0, index=c.index)
    for day, r in ratio[ratio < 0.6].items():
        factor[factor.index < day] *= r
    return factor


def load(data):
    cols = {k: {} for k in "OHLCV"}
    for p in sorted(data.glob("*.csv")):
        if NOT_STOCK.search(p.stem):
            continue
        try:
            df = pd.read_csv(p, usecols=["Date", "Open", "High", "Low", "Close", "Volume", "Series"],
                             parse_dates=["Date"], index_col="Date")
        except ValueError:
            continue
        df = df[df["Series"].fillna("EQ") == "EQ"]
        df = df[~df.index.duplicated()].sort_index()
        df = df[df.index >= "2008-01-01"]
        if len(df) < MIN_HISTORY:
            continue
        f = jump_factor(df["Close"].astype(float))
        s = p.stem.upper()
        for k, c in zip("OHLC", ("Open", "High", "Low", "Close")):
            cols[k][s] = (df[c].astype(float) * f).astype("float32")
        cols["V"][s] = df["Volume"].astype("float32")
    return {k: pd.DataFrame(v) for k, v in cols.items()}


def cagr(x, per=52):
    return (1 + x).prod() ** (per / len(x)) - 1 if len(x) else np.nan


def maxdd(x):
    eq = (1 + x).cumprod()
    return (eq / eq.cummax() - 1).min()


def sharpe(x, per=52):
    ex = x - ((1 + CASH_YEAR) ** (1 / per) - 1)
    return ex.mean() / ex.std() * np.sqrt(per)


def after_tax_cagr(x, per=52):
    """CAGR after 20.8% STCG on each calendar year's net gain, losses carried forward."""
    v, carry = 1.0, 0.0
    for _, g in x.groupby(x.index.year):
        gain = v * ((1 + g).prod() - 1)
        v += gain
        if gain < 0:
            carry -= gain
        else:
            taxable = max(0.0, gain - carry)
            carry = max(0.0, carry - gain)
            v -= STCG * taxable
    return v ** (per / len(x)) - 1


def equal(names, cap=None):
    if not len(names):
        return pd.Series(dtype=float)
    w = 1 / len(names) if cap is None else min(1 / len(names), cap)
    return pd.Series(w, index=names)


def main():
    data = Path(sys.argv[1])
    d = load(data)
    O, H, L, C, V = (d[k] for k in "OHLCV")
    print(f"{C.shape[1]} stocks loaded, {C.index[0].date()} to {C.index[-1].date()}", file=sys.stderr)
    days = C.index
    cf = C.ffill(limit=5)
    hist = C.notna().cumsum()
    medval = (C * V).rolling(60, min_periods=40).median()
    tv20 = (C * V).rolling(20, min_periods=15).mean()
    week = pd.Series(days.to_period("W"), index=days)
    sig_pos = np.flatnonzero((week != week.shift(-1)).to_numpy())  # last session of each week
    first_pos = np.flatnonzero((week != week.shift(1)).to_numpy())  # first session of each week

    score = {
        "W1 Reversal": -(cf / cf.shift(5) - 1),
        "W2 Short momentum": cf.shift(5) / cf.shift(68) - 1,
        "W3 52-week high": cf / cf.rolling(252, min_periods=200).max(),
        "W4 Mid M6 weekly": cf.shift(21) / cf.shift(126) - 1,
    }
    sma200 = cf.rolling(200, min_periods=200).mean()
    volratio = V.rolling(5, min_periods=3).mean() / V.rolling(50, min_periods=40).mean()
    prior_max = cf.rolling(252, min_periods=200).max().shift(1)  # read at each week's first session
    week_max = cf.groupby(week.to_numpy()).cummax()
    prev_c = cf.shift(1)
    tr = np.maximum(H - L, np.maximum((H - prev_c).abs(), (L - prev_c).abs()))
    atr = tr.rolling(14, min_periods=10).mean()
    sma50 = cf.rolling(50, min_periods=50).mean()
    sma150 = cf.rolling(150, min_periods=150).mean()
    hi252 = cf.rolling(252, min_periods=200).max()
    lo252 = cf.rolling(252, min_periods=200).min()
    trend_ok = ((cf > sma50) & (sma50 > sma150) & (sma150 > sma200) & (sma200 > sma200.shift(21))
                & (cf >= 1.3 * lo252) & (cf >= 0.75 * hi252))
    m6_raw = cf / cf.shift(126) - 1
    delta = cf.diff()
    up = delta.clip(lower=0).ewm(alpha=0.5, min_periods=2).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=0.5, min_periods=2).mean()
    rsi2 = 100 - 100 / (1 + up / down)

    names_all = list(RULES) + ["EW U", "EW Mid"]
    rows = {k: [] for k in names_all}
    held = {k: pd.Series(dtype=float) for k in names_all}
    picks_log = {}
    sec_rows, sec_trades = [], []  # secondary sim, filled after the rule is chosen
    week_data = []
    for j in range(len(sig_pos) - 1):
        s, s_next = sig_pos[j], sig_pos[j + 1]
        e, x = s + 1, s_next + 1
        if x >= len(days):
            break
        sday = days[s]
        if sday < pd.Timestamp("2009-06-01"):
            continue
        ok = (hist.iloc[s] >= MIN_HISTORY) & (medval.iloc[s] >= MIN_VALUE) & C.iloc[s].notna() & O.iloc[e].notna()
        ranked = medval.iloc[s][ok].sort_values(ascending=False).index
        uni = {"U": ranked[:250], "Mid": ranked[100:250]}
        entry = O.iloc[e]
        exitp = O.iloc[x].fillna(cf.iloc[x])
        r = (exitp / entry - 1).fillna(0.0)

        port = {"EW U": equal(uni["U"]), "EW Mid": equal(uni["Mid"])}
        ranks = {}
        for k in ("W1 Reversal", "W2 Short momentum", "W3 52-week high", "W4 Mid M6 weekly"):
            sc = score[k].iloc[s].reindex(uni[RULES[k]]).dropna().sort_values(ascending=False)
            ranks[k] = sc.index
            port[k] = equal(sc.index[:TOP])
        f = first_pos[first_pos <= s][-1]
        u = uni["U"]
        brk = ((week_max.iloc[s].reindex(u) > prior_max.iloc[f].reindex(u))
               & (cf.iloc[s].reindex(u) > sma200.iloc[s].reindex(u))
               & (volratio.iloc[s].reindex(u) >= 1.5))
        sc = volratio.iloc[s].reindex(u)[brk].sort_values(ascending=False)
        ranks["W5 Breakout"] = sc.index
        port["W5 Breakout"] = equal(sc.index[:TOP], cap=0.10)
        sc = m6_raw.iloc[s].reindex(u)[trend_ok.iloc[s].reindex(u).fillna(False)].dropna().sort_values(ascending=False)
        ranks["W6 Trend template"] = sc.index
        port["W6 Trend template"] = equal(sc.index[:TOP], cap=0.10)
        rs = rsi2.iloc[s].reindex(u)
        sc = rs[(rs < 10) & (cf.iloc[s].reindex(u) > sma200.iloc[s].reindex(u))].sort_values()
        ranks["W7 RSI(2) pullback"] = sc.index
        port["W7 RSI(2) pullback"] = equal(sc.index[:TOP], cap=0.10)

        for k, w in port.items():
            c = COST["Mid" if k in ("EW Mid", "W4 Mid M6 weekly") else "U"]
            allk = w.index.union(held[k].index)
            traded = (w.reindex(allk, fill_value=0) - held[k].reindex(allk, fill_value=0)).abs().sum()
            rr = r.reindex(w.index).fillna(0.0)
            cash = 1 - w.sum()
            gross = float((w * rr).sum()) + cash * CASH_WEEK
            rows[k].append((days[x], gross - c * traded, 0.5 * traded, len(w)))
            held[k] = w * (1 + rr) / (1 + gross)
        week_data.append((s, e, x, ranks))
        picks_log[sday] = {k: list(port[k].index) for k in RULES}

    res = {k: pd.DataFrame(v, columns=["date", "ret", "turn", "n"]).set_index("date") for k, v in rows.items()}
    res = {k: v[v.index >= START] for k, v in res.items()}

    # Baseline: monthly mid-cap M6 (the paper trial's rule), close to close, 0.20% a side.
    me = C.groupby(days.to_period("M")).tail(1).index
    me_px = cf.loc[me]
    m6_rows, mheld = [], pd.Series(dtype=float)
    for i in range(6, len(me) - 1):
        a, nxt = me[i], me[i + 1]
        si = days.get_loc(a)
        ok = (hist.iloc[si] >= MIN_HISTORY) & (medval.iloc[si] >= MIN_VALUE) & me_px.loc[a].notna()
        nm = medval.iloc[si][ok].sort_values(ascending=False).index[100:250]
        sc = (me_px.iloc[i - 1] / me_px.iloc[i - 6] - 1).reindex(nm).dropna()
        t = equal(sc.nlargest(TOP).index)
        rr = (me_px.loc[nxt] / me_px.loc[a] - 1).reindex(t.index).fillna(0.0)
        allk = t.index.union(mheld.index)
        turn = 0.5 * (t.reindex(allk, fill_value=0) - mheld.reindex(allk, fill_value=0)).abs().sum()
        gross = float((t * rr).sum())
        m6_rows.append((nxt, gross - 0.004 * turn))
        mheld = t * (1 + rr) / (1 + gross)
    m6 = pd.Series(dict(m6_rows))
    m6 = m6[m6.index >= START]

    nifty = pd.read_csv(data / "nifty 50.csv", usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
    nifty = nifty[~nifty.index.duplicated()].sort_index()

    def nifty_stats(lo, hi):
        n = nifty[(nifty.index >= lo) & (nifty.index <= hi)]
        yrs = (n.index[-1] - n.index[0]).days / 365.25
        gain = n.iloc[-1] / n.iloc[0] - 1
        wk = n.resample("W").last().pct_change().dropna()
        return (1 + gain) ** (1 / yrs) - 1, (1 + gain * (1 - LTCG)) ** (1 / yrs) - 1, maxdd(wk)

    end = res["EW U"].index[-1]
    periods = {"full": (START, end), "discovery": (START, "2019-12-31"), "holdout": ("2020-01-01", end)}

    def cut(x, p):
        lo, hi = periods[p]
        return x[(x.index >= lo) & (x.index <= hi)]

    print(f"\nWeeks {START} to {end.date()}: {len(res['EW U'])}")
    print("\n## Summary (net of costs; 'tax' = after 20.8% STCG each year)")
    print("| Portfolio | CAGR | after tax | worst DD | Sharpe | turnover/wk | holdout CAGR | holdout after tax |")
    print("|---|---|---|---|---|---|---|---|")

    def line(name, x, per, turn=None):
        h = cut(x, "holdout")
        print(f"| {name} | {cagr(x, per):+.1%} | {after_tax_cagr(x, per):+.1%} | {maxdd(x):.1%} | "
              f"{sharpe(x, per):.2f} | {'' if turn is None else f'{turn:.0%}'} | {cagr(h, per):+.1%} | "
              f"{after_tax_cagr(h, per):+.1%} |")

    for k in names_all:
        line(k, res[k]["ret"], 52, res[k]["turn"].mean())
    line("Monthly mid M6 (trial rule)", m6, 12, None)
    for p in ("full", "holdout"):
        g, t, dd = nifty_stats(*periods[p])
        print(f"| Nifty 50 buy & hold ({p}) | {g:+.1%} | {t:+.1%} (LTCG at end) | {dd:.1%} | | | | |")

    print("\n## Verdicts against own EW (weekly excess, after costs, before tax)")
    verdict = {}
    for k, uni in RULES.items():
        ex = (res[k]["ret"] - res[f"EW {uni}"]["ret"]).dropna()
        dsc, hold = cut(ex, "discovery"), cut(ex, "holdout")
        td = dsc.mean() / dsc.std() * np.sqrt(len(dsc))
        th = hold.mean() / hold.std() * np.sqrt(len(hold))
        yearly = ex.groupby(ex.index.year).apply(lambda q: (1 + q).prod() - 1)
        hy = yearly[yearly.index >= SPLIT]
        p1 = dsc.mean() > 0 and td > 2
        p2 = hold.mean() > 0 and (hy > 0).sum() >= 4
        h_tax = after_tax_cagr(cut(res[k]["ret"], "holdout"))
        p3 = h_tax > after_tax_cagr(cut(m6, "holdout"), 12) and h_tax > nifty_stats(*periods["holdout"])[1]
        verdict[k] = (p1 and p2, p3, td)
        print(f"\n### {k} (vs EW {uni})")
        print(f"discovery {dsc.mean():+.3%}/wk (t {td:+.1f}, n {len(dsc)}) -> {'pass' if p1 else 'fail'}; "
              f"holdout {hold.mean():+.3%}/wk (t {th:+.1f}), years beating EW {(hy > 0).sum()}/{len(hy)} "
              f"-> {'pass' if p2 else 'fail'}; beats monthly M6 and Nifty after tax in holdout: {'yes' if p3 else 'no'}")
        print("yearly excess: " + ", ".join(f"{y}:{v:+.0%}" for y, v in yearly.items()))

    passed = [k for k, v in verdict.items() if v[0]]
    chosen = max(passed or verdict, key=lambda k: verdict[k][2])
    print(f"\nPASSED: {passed or 'none'}; chosen for the secondary run: {chosen}"
          f"{'' if passed else ' (highest discovery t, FAILED the bar)'}")
    if passed:
        print(f"Chosen beats monthly M6 and Nifty after tax in holdout: {'yes' if verdict[chosen][1] else 'no'}")

    # Secondary: top 5 of the chosen rule under the risk rules.
    eq, rows2, trades = 1.0, [], []
    for s, e, x, ranks in week_data:
        if days[x] < pd.Timestamp(START):
            continue
        cands = [n for n in ranks[chosen]
                 if cf.iat[s, C.columns.get_loc(n)] > sma200.iat[s, C.columns.get_loc(n)]
                 and tv20.iat[s, C.columns.get_loc(n)] >= 1e8][:5]
        pnl, used = 0.0, 0.0
        for n in cands:
            ci = C.columns.get_loc(n)
            ent, a = O.iat[e, ci], atr.iat[s, ci]
            if not (ent > 0 and a > 0):
                continue
            stop = ent - 2 * a
            risk_frac = (ent - stop) / ent + 0.005
            notional = min(0.01 / risk_frac, 0.15) * eq
            fill = None
            for t in range(e, x):
                o, lo = O.iat[t, ci], L.iat[t, ci]
                if t > e and o <= stop:
                    fill = o
                    break
                if lo <= stop:
                    fill = stop
                    break
            if fill is None:
                fill = O.iat[x, ci] if not np.isnan(O.iat[x, ci]) else cf.iat[x, ci]
            ret = fill / ent - 1
            p = notional * ret - notional * COST["U"] * (2 + ret)
            pnl += p
            used += notional
            trades.append((days[e], n, ret, (fill - ent) / (ent - stop)))
        pnl += (eq - used) * CASH_WEEK
        rows2.append((days[x], pnl / eq))
        eq += pnl
    sec = pd.Series(dict(rows2))
    tr_df = pd.DataFrame(trades, columns=["date", "sym", "ret", "R"])
    print(f"\n## Secondary: {chosen}, top 5 under risk rules (1% risk, 2 ATR stop, 15% cap, R8 filter)")
    print(f"CAGR {cagr(sec):+.1%}, after tax {after_tax_cagr(sec):+.1%}, worst DD {maxdd(sec):.1%}, "
          f"Sharpe {sharpe(sec):.2f}; holdout CAGR {cagr(cut(sec, 'holdout')):+.1%}")
    print(f"trades {len(tr_df)}, hit rate {(tr_df['ret'] > 0).mean():.0%}, average R {tr_df['R'].mean():+.2f}, "
          f"stopped {(tr_df['R'] <= -0.99).mean():.0%}, avg capital in use {tr_df.groupby('date').size().mean():.1f} names")

    last = max(picks_log)
    print(f"\n## Picks from the last signal day in the data, {last.date()} (for reference)")
    for k, v in picks_log[last].items():
        print(f"{k}: {', '.join(v)}")


if __name__ == "__main__":
    main()
