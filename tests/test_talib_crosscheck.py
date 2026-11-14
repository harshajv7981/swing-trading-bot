"""Cross-check our hand-written indicators against TA-Lib. Dev-only: skipped when TA-Lib is absent.

TA-Lib seeds Wilder/EMA averages with a simple mean, ours with the first value, so early bars differ.
On a long series that difference decays to nothing, so only the last value is compared.
"""
import numpy as np
import pandas as pd
import pytest

talib = pytest.importorskip("talib")

from swingbot.risk.exits import atr
from swingbot.technicals import indicators

N = 600


@pytest.fixture(scope="module")
def bars():
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, N)))
    open_ = close * (1 + rng.normal(0, 0.005, N))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, N))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, N))
    volume = rng.integers(10_000, 100_000, N).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=N)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _ta(fn, *cols, **kw):
    out = fn(*(c.to_numpy() for c in cols), **kw)
    return float((out[0] if isinstance(out, tuple) else out)[-1])


def test_stop_atr_matches_talib(bars):
    assert atr(bars, 14) == pytest.approx(_ta(talib.ATR, bars.high, bars.low, bars.close, timeperiod=14), rel=1e-6)


def test_indicator_sheet_matches_talib(bars):
    got = indicators(bars)
    c, h, lo = bars.close, bars.high, bars.low
    macd, signal, hist = (float(x[-1]) for x in talib.MACD(c.to_numpy(), 12, 26, 9))
    upper, mid, lower = (float(x[-1]) for x in talib.BBANDS(c.to_numpy(), 20, 2, 2))
    want = {
        "rsi_14": _ta(talib.RSI, c, timeperiod=14),
        "atr_14": _ta(talib.ATR, h, lo, c, timeperiod=14),
        "ema_10": _ta(talib.EMA, c, timeperiod=10),
        "sma_50": _ta(talib.SMA, c, timeperiod=50),
        "sma_200": _ta(talib.SMA, c, timeperiod=200),
        "macd": macd, "macd_signal": signal, "macd_hist": hist,
        "boll_upper": upper, "boll_mid": mid, "boll_lower": lower,
    }
    for name, value in want.items():
        assert got[name] == pytest.approx(value, rel=1e-6, abs=1e-9), name
