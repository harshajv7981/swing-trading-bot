"""Swing highs and lows with no look-ahead.

A bar is a swing high when its high is above the k bars on each side (ties on the
left allowed, so a flat top counts once, at its last bar). That can only be known
once the k bars after it have closed, so each swing is stamped on its confirmation
bar, k bars after the pivot, never on the pivot bar itself. smartmoneyconcepts'
swing_highs_lows stamps the pivot bar, which leaks future bars into backtests.
"""

from __future__ import annotations

import pandas as pd


def confirmed_swings(bars: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Columns swing_high / swing_low: the pivot's price on the bar the swing became known, else NaN."""
    if k < 1:
        raise ValueError("k must be at least 1")
    out = {}
    for col, name, sign in (("high", "swing_high", 1), ("low", "swing_low", -1)):
        s = sign * bars[col].astype(float)
        pivot = s.shift(k)
        left = s.shift(k + 1).rolling(k).max()  # the k bars before the pivot
        right = s.rolling(k).max()  # the k bars after it, the newest being today
        is_swing = (pivot >= left) & (pivot > right)
        out[name] = (sign * pivot).where(is_swing)
    return pd.DataFrame(out, index=bars.index)
