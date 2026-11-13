"""Extra checks on the TradingAgents calls, borrowed from FINSABER
(docs/research/finsaber-review.md). Added 2026-11-13, before either test is judged.
They are reported beside each test's result and do not change either pass bar.

* Random calls: how often random buy/sell calls (large-cap) or random picks of the
  same size (mid-cap), on the same stocks and weeks, did at least as well.
* Market split: the result in rising, flat and falling weeks of the index.
"""

from __future__ import annotations

import numpy as np

DRAWS = 10_000
BAND = 0.01   # an index move within +-1% over the 5 sessions counts as a flat market


def random_sign_p(alphas, signs, draws: int = DRAWS, seed: int = 0) -> float:
    """Share of random-direction call sets whose mean signed alpha is >= the real calls'."""
    a, s = np.asarray(alphas, float), np.asarray(signs, float)
    actual = (a * s).mean()
    rand = np.random.default_rng(seed).choice([-1.0, 1.0], size=(draws, len(a)))
    return float((1 + ((rand * a).mean(axis=1) >= actual - 1e-12).sum()) / (draws + 1))


def random_picks_p(weeks: list[tuple[list[float], int, float]], draws: int = DRAWS, seed: int = 0) -> float:
    """weeks: (that week's 5-day returns of every stock, picks made, the real picks' mean).
    Share of random same-size picks, week by week, whose mean over weeks is >= the real one."""
    rng = np.random.default_rng(seed)
    actual = np.mean([m for _, _, m in weeks])
    total = np.zeros(draws)
    for rets, k, _ in weeks:
        r = np.asarray(rets, float)
        pick = rng.random((draws, len(r))).argsort(axis=1)[:, :k]
        total += r[pick].mean(axis=1)
    return float((1 + (total / len(weeks) >= actual - 1e-12).sum()) / (draws + 1))


def by_market(pairs) -> dict[str, list[float]]:
    """pairs: (result, index move over the same days). Results grouped by the index's direction."""
    out: dict[str, list[float]] = {"rising": [], "flat": [], "falling": []}
    for x, move in pairs:
        out["rising" if move > BAND else "falling" if move < -BAND else "flat"].append(x)
    return out


def lines(p: float | None, split: dict[str, list[float]], what: str, index: str) -> list[str]:
    out = []
    if p is not None:
        out.append(f"Random check: random {what} on the same stocks and weeks did at least as well "
                   f"{p:.0%} of the time ({DRAWS:,} draws). Under 5% means the calls beat chance.")
    parts = [f"{k} {len(v)}, mean {sum(v) / len(v):+.2%}" if v else f"{k} 0"
             for k, v in split.items()]
    out.append(f"By market ({index} over the same 5 sessions, flat within +-{BAND:.0%}): " + "; ".join(parts) + ".")
    return out
