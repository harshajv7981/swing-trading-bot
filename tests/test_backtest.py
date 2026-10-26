from datetime import datetime

import pandas as pd
import pytest

from swingbot.backtest import entry_index, measure, run_backtest
from swingbot.metrics import summarise
from swingbot.models import IST, NewsEvent, Sentiment, Signal
from swingbot.swarm.base import SwarmEngine


def bars(closes, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(closes))
    df = pd.DataFrame({"close": closes}, index=idx)
    df["open"] = df["close"] - 1
    df["high"] = df["close"] + 2
    df["low"] = df["open"] - 2
    df["volume"] = 1000.0
    return df[["open", "high", "low", "close", "volume"]]


def ev(ts, ticker="ABC", eid="x"):
    return NewsEvent(eid, ticker, datetime.fromisoformat(ts).replace(tzinfo=IST), "headline")


B = bars([100, 101, 102, 103, 104, 105, 106, 107])  # Mon 5 Jan .. Wed 14 Jan 2026


@pytest.mark.parametrize(
    "ts, expected_date",
    [
        ("2026-01-05 08:00", "2026-01-05"),  # before open: same day
        ("2026-01-05 09:15", "2026-01-06"),  # at the open: too late for that open
        ("2026-01-05 15:45", "2026-01-06"),  # after close: next session
        ("2026-01-10 11:00", "2026-01-12"),  # Saturday: Monday
        ("2026-01-04 20:00", "2026-01-05"),  # Sunday evening: Monday
    ],
)
def test_entry_never_precedes_news(ts, expected_date):
    i = entry_index(B, ev(ts))
    assert B.index[i] == pd.Timestamp(expected_date)


def test_entry_handles_utc_timestamps():
    # 03:00 UTC = 08:30 IST, before the open
    e = NewsEvent("u", "ABC", datetime.fromisoformat("2026-01-05T03:00:00+00:00"), "h")
    assert B.index[entry_index(B, e)] == pd.Timestamp("2026-01-05")


def test_measure_three_sessions():
    out = measure(B, ev("2026-01-05 08:00"), sessions=3)
    assert out.entry_price == 99  # open of day 1
    assert out.exit_price == 102  # close of day 3
    assert out.ret == pytest.approx(102 / 99 - 1)
    assert out.exit_date == pd.Timestamp("2026-01-07")


def test_measure_returns_none_past_end_of_data():
    assert measure(B, ev("2026-01-14 08:00"), sessions=3) is None


class FixedEngine(SwarmEngine):
    name = "fixed"

    def __init__(self, calls):
        self.calls = calls

    def simulate(self, event):
        s = self.calls[event.event_id]
        return Signal(event.ticker, s, 0.8, "72h", score=float(s.direction))


class DictPrices:
    def __init__(self, data):
        self.data = data

    def daily(self, t):
        if t not in self.data:
            raise FileNotFoundError(t)
        return self.data[t]


def test_run_backtest_scores_hits_and_costs():
    up, down = bars([100, 101, 102, 103, 104]), bars([100, 99, 98, 97, 96])
    events = [ev("2026-01-05 08:00", "UP", "a"), ev("2026-01-05 08:00", "DOWN", "b"),
              ev("2026-01-05 08:00", "UP", "c"), ev("2026-01-05 08:00", "NONE", "d")]
    engine = FixedEngine({"a": Sentiment.BULLISH, "b": Sentiment.BULLISH,
                          "c": Sentiment.NEUTRAL, "d": Sentiment.BEARISH})
    res = run_backtest(events, engine, DictPrices({"UP": up, "DOWN": down}), cost_bps=10)

    r = res.set_index("event_id")
    assert r.loc["a", "hit"] == True  # noqa: E712
    assert r.loc["b", "hit"] == False  # noqa: E712
    assert r.loc["a", "trade_ret"] == pytest.approx(102 / 99 - 1 - 0.001)
    assert r.loc["c", "trade_ret"] == 0.0
    assert r.loc["d", "status"].startswith("no_prices")

    s = summarise(res)
    assert s["scored"] == 3 and s["skipped"] == 1
    assert s["directional_calls"] == 2 and s["hit_rate"] == 0.5


def test_signal_horizon_and_json():
    s = Signal("RELIANCE", Sentiment.BULLISH, 0.88, "72h")
    assert s.horizon_hours == 72
    assert s.to_json() == {"ticker": "RELIANCE", "sentiment": "bullish", "confidence": 0.88, "time_horizon": "72h"}
    with pytest.raises(ValueError):
        Signal("X", Sentiment.BULLISH, 1.5)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        NewsEvent("n", "ABC", datetime(2026, 1, 5, 8), "h")
