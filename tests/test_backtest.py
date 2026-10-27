from datetime import datetime

import pandas as pd
import pytest

from swingbot.backtest import entry_index, measure, run_backtest, simulate_long
from swingbot.risk.exits import PlanRefused
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
        ("2026-01-05 09:07", "2026-01-05"),  # in pre-open order entry: same day
        ("2026-01-05 09:10", "2026-01-06"),  # after pre-open order entry closed: next session
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


def history(window, n=20, start="2025-12-01"):
    """`n` quiet bars (open 100, high 101, low 99, close 100: ATR 2) then `window` (o, h, l, c) bars.

    With the default atr:2 stop and 2R target, an entry at 100 plans stop 96, target 108.
    """
    rows = [(100.0, 101.0, 99.0, 100.0)] * n + list(window)
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"])
    df["volume"] = 1000.0
    return df


@pytest.mark.parametrize(
    "window, reason, exit_price",
    [
        ([(100, 109, 99, 107)], "target", 108),  # target touched on the entry day
        ([(100, 101, 95, 97)], "stop", 96),  # stop touched on the entry day
        ([(100, 109, 95, 100)], "stop", 96),  # both in one day: the stop, to be safe
        ([(100, 101, 99, 100), (93, 94, 90, 92)], "gap_stop", 93),  # gap down through the stop
        ([(100, 101, 99, 100), (110, 112, 109, 111)], "gap_target", 110),  # gap up through the target
        ([(100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103)], "time", 103),
    ],
)
def test_simulate_long_exits(window, reason, exit_price):
    b = history(window)
    t = simulate_long(b, 20, sessions=3, trail=False)
    assert (t.entry, t.stop, t.target) == (100, 96, 108)
    assert t.exit_reason == reason
    assert t.exit_price == exit_price


def test_simulate_long_plan_uses_only_bars_before_entry():
    # A huge range on the entry day must not widen the stop: ATR comes from the history only.
    b = history([(100, 130, 70, 100)])
    t = simulate_long(b, 20, sessions=1, trail=False)
    assert t.stop == 96 and t.exit_reason == "stop"


def test_simulate_long_trails_the_stop():
    # Close at 105 is past +1R (104): the stop moves up to breakeven at least, so a dip
    # to 99 next day stops the trade near 100 instead of riding down to 96.
    b = history([(100, 101, 99, 100), (101, 106, 100, 105), (104, 104, 98, 99)])
    t = simulate_long(b, 20, sessions=3)
    assert t.exit_reason == "stop" and t.final_stop >= 100 and t.exit_price >= 100
    untrailed = simulate_long(b, 20, sessions=3, trail=False)
    assert untrailed.exit_reason == "time" and untrailed.exit_price == 99


def test_simulate_long_refuses_without_history():
    with pytest.raises(PlanRefused):
        simulate_long(history([(100, 101, 99, 100)], n=5), 5, sessions=1)


def test_run_backtest_long_only_trades_and_accuracy():
    ts = "2025-12-29 08:00"  # Monday, before the open: enters at the open of bar 20
    up = history([(100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103)])
    down = history([(100, 101, 99, 99), (99, 100, 98, 98), (98, 99, 97, 97)])
    crash = history([(100, 101, 95, 96)] * 3)
    events = [ev(ts, "UP", "a"), ev(ts, "DOWN", "b"), ev(ts, "UP", "c"), ev(ts, "DOWN", "d"),
              ev(ts, "CRASH", "e"), ev(ts, "SHORT", "f"), ev(ts, "NONE", "g")]
    engine = FixedEngine({"a": Sentiment.BULLISH, "b": Sentiment.BULLISH, "c": Sentiment.NEUTRAL,
                          "d": Sentiment.BEARISH, "e": Sentiment.BULLISH, "f": Sentiment.BULLISH,
                          "g": Sentiment.BEARISH})
    prices = DictPrices({"UP": up, "DOWN": down, "CRASH": crash, "SHORT": bars([100, 101, 102, 103, 104])})
    res = run_backtest(events, engine, prices, cost_bps=10)
    r = res.set_index("event_id")

    assert r.loc["a", "entry_date"] == "2025-12-29"
    assert r.loc["a", "hit"] == True  # noqa: E712
    assert r.loc["a", "traded"] == True and r.loc["a", "exit_reason"] == "time"  # noqa: E712
    assert r.loc["a", "trade_ret"] == pytest.approx(103 / 100 - 1 - 0.001)
    assert r.loc["b", "hit"] == False  # noqa: E712
    assert r.loc["b", "trade_ret"] == pytest.approx(97 / 100 - 1 - 0.001)
    assert r.loc["c", "trade_ret"] == 0.0 and r.loc["c", "traded"] == False  # noqa: E712

    # A right bearish call is still accurate, but the long-only bot makes nothing on it.
    assert r.loc["d", "hit"] == True  # noqa: E712
    assert r.loc["d", "traded"] == False and r.loc["d", "trade_ret"] == 0.0  # noqa: E712
    assert r.loc["d", "no_trade_reason"].startswith("bearish")

    # The stop caps the loss at 96 even though the stock closes lower at the horizon.
    assert r.loc["e", "exit_reason"] == "stop" and r.loc["e", "trade_exit_price"] == 96
    assert r.loc["e", "trade_ret"] == pytest.approx(96 / 100 - 1 - 0.001)
    assert r.loc["e", "trade_r"] == pytest.approx(-1.0)

    # Too little history for an ATR: the planner refuses, so no trade.
    assert r.loc["f", "traded"] == False  # noqa: E712
    assert r.loc["f", "no_trade_reason"].startswith("plan refused")
    assert r.loc["g", "status"].startswith("no_prices")

    s = summarise(res)
    assert s["scored"] == 6 and s["skipped"] == 1
    assert s["directional_calls"] == 5 and s["hit_rate"] == pytest.approx(3 / 5)
    assert s["trades"] == 3 and s["win_rate"] == pytest.approx(1 / 3)
    assert s["calls_not_traded"] == {"bearish": 1, "plan refused": 1}
    assert s["exit_reasons"] == {"time": 2, "stop": 1}
    assert "bullish" in s["by_sentiment"] and "avg_trade_ret" not in s["by_sentiment"]["bullish"]


def test_signal_horizon_and_json():
    s = Signal("RELIANCE", Sentiment.BULLISH, 0.88, "72h")
    assert s.horizon_hours == 72
    assert s.to_json() == {"ticker": "RELIANCE", "sentiment": "bullish", "confidence": 0.88, "time_horizon": "72h"}
    with pytest.raises(ValueError):
        Signal("X", Sentiment.BULLISH, 1.5)


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        NewsEvent("n", "ABC", datetime(2026, 1, 5, 8), "h")
