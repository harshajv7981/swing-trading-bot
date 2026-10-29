from datetime import datetime

import numpy as np
import pandas as pd

from swingbot.models import IST, NewsEvent
from swingbot.technicals import indicators, known_bars, snapshot

from helpers import DictPrices, bars


def ev(ts, ticker="UP"):
    return NewsEvent("e1", ticker, datetime.fromisoformat(ts).replace(tzinfo=IST), "headline")


def walk(n=260, start="2025-01-01", seed=1):
    rng = np.random.default_rng(seed)
    return bars(list(100 * np.cumprod(1 + rng.normal(0, 0.01, n))), start=start)


def test_known_bars_never_include_the_news_day_close_before_1530():
    b = bars([100, 101, 102, 103, 104])  # Mon 5 .. Fri 9 Jan 2026
    assert known_bars(b, ev("2026-01-07 08:00")).index[-1] == pd.Timestamp("2026-01-06")
    assert known_bars(b, ev("2026-01-07 15:29")).index[-1] == pd.Timestamp("2026-01-06")
    assert known_bars(b, ev("2026-01-07 15:30")).index[-1] == pd.Timestamp("2026-01-07")
    assert known_bars(b, ev("2026-01-10 11:00")).index[-1] == pd.Timestamp("2026-01-09")  # Saturday


def test_future_bars_do_not_change_the_snapshot():
    b = walk()
    news = ev("2025-09-10 10:00")
    before = snapshot(DictPrices({"UP": b[b.index < "2025-09-10"]}), news)
    shocked = b.copy()
    shocked.loc[shocked.index >= "2025-09-10", ["open", "high", "low", "close"]] *= 3
    assert snapshot(DictPrices({"UP": shocked}), news) == before


def test_indicator_values_on_known_series():
    rising = bars(list(range(100, 160)))  # +1 every session
    v = indicators(rising)
    assert v["rsi_14"] == 100.0
    assert v["sma_50"] == np.mean(range(110, 160))
    assert v["sma_200"] is None  # not enough history
    assert v["macd"] > 0 and v["ret_1d"] == 159 / 158 - 1
    assert abs(v["atr_14"] - 5.0) < 1e-9  # high - low is 5 every day in the fixture


def test_missing_or_short_history_is_unavailable_not_flat():
    assert "unavailable" in snapshot(DictPrices({}), ev("2026-01-07 08:00"))
    assert "unavailable (only 3 sessions" in snapshot(DictPrices({"UP": bars([1, 2, 3])}), ev("2026-01-07 15:45"))


