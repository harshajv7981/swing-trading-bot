"""The research kit: the checks every study in docs/research uses, in one importable place.

Prices come in through `load` (eod2-style CSVs, one file per symbol, splits removed by `adjust_jumps`).
A study then reports its returns through these:

- `tstat`: the t-statistic of a series of excess returns.
- `after_tax_cagr`: short-term tax on each calendar year's net gain, losses carried forward. This is how a
  strategy that trades every month is taxed in India.
- `hold_after_tax_cagr`: long-term tax once, at the end, as on a fund held over a year.
- `deflated_sharpe`: Bailey and Lopez de Prado (2014), so a result is read against how many were tried.
- `ledger_missing`: pre-registrations not yet listed in the trials ledger.

The study scripts in scripts/research keep their own frozen copies so old results stay reproducible;
tests/test_research.py checks the tax function here against theirs.
"""

from __future__ import annotations

import math
from pathlib import Path
from statistics import NormalDist

import pandas as pd

from .momentum import adjust_jumps, load  # noqa: F401  (re-exported: the kit's price loader)

STCG, LTCG = 0.208, 0.13  # FY 2026-27 listed equity rates with the 4% cess


def tstat(x: pd.Series) -> float:
    return float(x.mean() / x.std() * math.sqrt(len(x)))


def after_tax_cagr(x: pd.Series, per: int = 12, rate: float = STCG) -> float:
    """CAGR after `rate` on each calendar year's net gain, losses carried forward. `x` holds period returns."""
    v, carry = 1.0, 0.0
    for _, g in x.groupby(x.index.year):
        gain = v * ((1 + g).prod() - 1)
        v += gain
        if gain < 0:
            carry -= gain
        else:
            v -= rate * max(0.0, gain - carry)
            carry = max(0.0, carry - gain)
    return v ** (per / len(x)) - 1


def hold_after_tax_cagr(x: pd.Series, per: int = 12, rate: float = LTCG) -> float:
    """CAGR of buying once and selling at the end, with `rate` on the whole gain (no exemption)."""
    end = float((1 + x).prod())
    return (end - rate * max(0.0, end - 1)) ** (per / len(x)) - 1


def deflated_sharpe(x: pd.Series, trial_srs: pd.Series, trials: int) -> tuple[float, float, float]:
    """Bailey and Lopez de Prado (2014), per-period Sharpe. Returns (DSR, SR, SR0).

    `trial_srs` are the per-period Sharpe ratios of the variants tried; `trials` is how many were tried."""
    nd, g = NormalDist(), 0.5772156649
    sr = x.mean() / x.std()
    z = (x - x.mean()) / x.std(ddof=0)
    skew, kurt = (z ** 3).mean(), (z ** 4).mean()
    sr0 = trial_srs.std() * ((1 - g) * nd.inv_cdf(1 - 1 / trials) + g * nd.inv_cdf(1 - 1 / (trials * math.e)))
    dsr = nd.cdf((sr - sr0) * math.sqrt(len(x) - 1) / math.sqrt(1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return dsr, sr, sr0


def ledger_missing(folder: Path, ledger: str = "trials-ledger.md") -> list[str]:
    """`*-prereg.md` files in `folder` that the ledger does not link to."""
    text = (folder / ledger).read_text()
    return [p.name for p in sorted(folder.glob("*-prereg.md")) if f"]({p.name})" not in text]
