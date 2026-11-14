"""A worked example of the research kit (docs/kit.md), on made-up prices.

    python examples/kit_example.py              # generates random prices: never a study on real data
    python examples/kit_example.py path/to/csvs # your own eod2-style files, AFTER writing a prereg

The toy rule (hold the 10 best 6-1 month performers, monthly) is only there to show the calls:
load prices, apply costs, then judge the excess with a t-stat, after-tax CAGR against a held
benchmark, and a deflated Sharpe that counts every lookback tried.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from swingbot import research

COST, TOP, LOOKBACKS = 0.004, 10, (3, 6, 9, 12)  # 0.40% per unit of one-way turnover


def fake_prices(folder: Path, n: int = 40, seed: int = 7) -> None:
    rng, days = np.random.default_rng(seed), pd.bdate_range("2012-01-02", "2020-12-31")
    for k in range(n):
        close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, len(days))))
        pd.DataFrame({"Date": days.strftime("%Y-%m-%d"), "Close": close.round(2),
                      "Volume": 1_000_000, "Series": "EQ"}).to_csv(folder / f"stock{k}.csv", index=False)


def rule(me_px: pd.DataFrame, lookback: int) -> pd.Series:
    """Monthly net returns of the top TOP by lookback-1 month return, equal weight."""
    rets, held = [], pd.Series(dtype=float)
    for i in range(lookback, len(me_px) - 1):
        score = (me_px.iloc[i - 1] / me_px.iloc[i - lookback] - 1).dropna()
        target = pd.Series(1 / TOP, index=score.nlargest(TOP).index)
        allk = target.index.union(held.index)
        turn = 0.5 * (target.reindex(allk, fill_value=0) - held.reindex(allk, fill_value=0)).abs().sum()
        r = (me_px.iloc[i + 1] / me_px.iloc[i] - 1).reindex(target.index).fillna(0.0)
        gross = float((target * r).sum())
        rets.append((me_px.index[i + 1], gross - COST * turn))
        held = target * (1 + r) / (1 + gross)
    return pd.Series(dict(rets))


def main():
    if len(sys.argv) > 1:
        data = Path(sys.argv[1])
    else:
        data = Path(tempfile.mkdtemp())
        fake_prices(data)
    px, _ = research.load(data)
    me_px = px.groupby(px.index.to_period("M")).tail(1)
    ew = me_px.pct_change().mean(axis=1)  # the baseline: every stock, equal weight

    tried = {lb: rule(me_px, lb) for lb in LOOKBACKS}
    x = tried[6]  # the variant fixed in the (imaginary) prereg
    ex = (x - ew.reindex(x.index)).dropna()
    srs = pd.Series({lb: (s - ew.reindex(s.index)).mean() / (s - ew.reindex(s.index)).std() for lb, s in tried.items()})
    dsr, sr, sr0 = research.deflated_sharpe(ex, srs, trials=len(LOOKBACKS))

    print(f"{px.shape[1]} stocks, {len(x)} months")
    print(f"excess over equal weight: {ex.mean():+.2%}/month, t {research.tstat(ex):+.1f}")
    print(f"rule after short-term tax: {research.after_tax_cagr(x):+.1%} a year")
    print(f"equal weight held, long-term tax at the end: {research.hold_after_tax_cagr(ew.reindex(x.index)):+.1%}")
    print(f"deflated Sharpe {dsr:.2f} (Sharpe {sr:.3f} against {sr0:.3f} expected from the best of "
          f"{len(LOOKBACKS)} by luck)")


if __name__ == "__main__":
    main()
