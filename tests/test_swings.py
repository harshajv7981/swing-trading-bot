import numpy as np
import pandas as pd
import pytest

from swingbot.swings import confirmed_swings


def _bars(high, low=None):
    high = pd.Series(high, dtype=float, index=pd.bdate_range("2024-01-01", periods=len(high)))
    return pd.DataFrame({"high": high, "low": high - 1 if low is None else pd.Series(low, index=high.index, dtype=float)})


def test_swing_high_is_stamped_k_bars_after_the_pivot():
    sw = confirmed_swings(_bars([1, 2, 3, 9, 3, 2, 1, 1]), k=3)
    assert sw["swing_high"].dropna().to_dict() == {sw.index[6]: 9.0}  # pivot at bar 3, known at bar 6


def test_swing_low_mirrors_high():
    sw = confirmed_swings(_bars([5] * 8, low=[5, 4, 3, 1, 3, 4, 5, 5]), k=3)
    assert sw["swing_low"].dropna().to_dict() == {sw.index[6]: 1.0}


def test_flat_top_counts_once():
    sw = confirmed_swings(_bars([1, 2, 9, 9, 2, 1, 1]), k=2)
    assert sw["swing_high"].dropna().to_dict() == {sw.index[5]: 9.0}  # the second 9, confirmed 2 bars later


def test_no_swing_without_k_bars_on_both_sides():
    sw = confirmed_swings(_bars([1, 9, 1, 1]), k=2)  # only 1 bar left of the 9
    assert sw["swing_high"].isna().all()


def test_never_uses_future_bars():
    """Each row must be the same whether or not later bars exist."""
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 1, 300))
    bars = _bars(close + rng.uniform(0, 1, 300), low=close - rng.uniform(0, 1, 300))
    full = confirmed_swings(bars, k=5)
    assert full["swing_high"].notna().sum() > 5
    for t in range(0, 300, 7):
        pd.testing.assert_frame_equal(confirmed_swings(bars.iloc[: t + 1], k=5), full.iloc[: t + 1])


def test_k_must_be_positive():
    with pytest.raises(ValueError):
        confirmed_swings(_bars([1, 2, 3]), k=0)
