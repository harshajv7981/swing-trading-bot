"""US large-cap momentum (docs/research/us-momentum-prereg.md): data, ranking,
replay, order planning, booking time and an idempotent booking."""

import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from alpaca_fake import FakeAlpaca

from swingbot import us_momentum
from swingbot.broker.alpaca import AlpacaAPI, AlpacaBroker, AlpacaConfig
from swingbot.us_momentum import (
    AlpacaData,
    booking_month,
    month_ends,
    parse_sp500,
    plan_orders,
    rank,
    replay,
)


def closes(rets: dict[str, float], months: int = 15, start="2024-09-30") -> pd.DataFrame:
    """Month-end closes where each symbol compounds at a fixed monthly rate."""
    idx = pd.date_range(start, periods=months, freq="BME")
    return pd.DataFrame({s: [100 * (1 + r) ** i for i in range(months)] for s, r in rets.items()}, index=idx)


# --- data -----------------------------------------------------------------------------


def test_sp500_members_are_read_from_the_constituents_table():
    html = ("<table id='other'><tr><td>NOPE</td></tr></table>"
            "<table class='wikitable' id='constituents'><tr><th>Symbol</th><th>Security</th></tr>"
            "<tr><td><a href='x'>MMM</a></td><td>3M</td></tr>"
            "<tr><td><a href='y'>BRK.B</a>\n</td><td>Berkshire</td></tr></table>")
    assert parse_sp500(html) == ["BRK.B", "MMM"]
    with pytest.raises(ValueError):
        parse_sp500("<table id='constituents'></table>")


def test_daily_closes_are_paged_and_dated_in_new_york():
    pages = [
        {"bars": {"AAPL": [{"t": "2026-10-29T04:00:00Z", "c": 10.0}], "SPY": [{"t": "2026-10-29T04:00:00Z", "c": 5.0}]},
         "next_page_token": "p2"},
        {"bars": {"AAPL": [{"t": "2026-10-30T04:00:00Z", "c": 11.0}]}, "next_page_token": None},
    ]
    seen = []

    def transport(method, url, headers, body):
        seen.append(url)
        assert "data.alpaca.markets/v2/stocks/bars" in url and "adjustment=all" in url
        return 200, json.dumps(pages[len(seen) - 1]).encode()

    data = AlpacaData("kid", "sec", transport=transport)
    df = data.daily_closes(["AAPL", "SPY"], date(2026, 10, 1), date(2026, 10, 30))
    assert list(df.index) == [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30")]
    assert df.loc["2026-10-30", "AAPL"] == 11.0 and pd.isna(df.loc["2026-10-30", "SPY"])
    assert "page_token=p2" in seen[1]


def test_month_ends_take_the_last_trading_day_of_each_month():
    idx = pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-29", "2026-10-30", "2026-11-02"])
    m = month_ends(pd.DataFrame({"A": [1, 2, 3, 4, 5]}, index=idx))
    assert list(m.index) == list(pd.to_datetime(["2026-09-30", "2026-10-30", "2026-11-02"]))
    assert list(m["A"]) == [2, 4, 5]


# --- the rule ------------------------------------------------------------------------------


def test_rank_uses_12_1_momentum_skipping_the_latest_month():
    m = closes({"A": 0.02, "B": 0.01, "C": 0.03, "D": -0.01})
    m.iloc[-1, m.columns.get_loc("D")] = 10_000            # a jump in the latest month is skipped
    picks, signal = rank(m, m.index[-1], members=["A", "B", "C", "D"], n=2)
    assert picks == ["C", "A"]
    assert signal["C"] == pytest.approx(1.03 ** 11 - 1)
    m2 = m.copy()
    m2.iloc[:3, m2.columns.get_loc("C")] = float("nan")   # under 13 month-ends of history
    assert rank(m2, m2.index[-1], members=["A", "B", "C", "D"], n=2)[0] == ["A", "B"]
    assert rank(m, m.index[-1], members=["A", "B"], n=5)[0] == ["A", "B"]


def test_replay_books_each_month_with_costs_and_benchmarks():
    m = closes({"A": 0.02, "B": 0.01, "C": 0.03, "D": -0.01, "SPY": 0.01, "RSP": 0.005, "SPMO": 0.015}, months=16)
    rows = replay(m, members=["A", "B", "C", "D"], first=m.index[12], last=m.index[14], n=2, cost=0.001)
    assert [r["booked"] for r in rows] == [str(d.date()) for d in m.index[12:15]]
    first = rows[0]
    assert first["picks"] == ["C", "A"] and first["turnover"] == pytest.approx(1.0)
    assert first["ret"] == pytest.approx((1.02 + 1.03) / 2 - 1 - 0.001 * 1.0)
    assert first["bench"]["SPY"] == pytest.approx(0.01)
    assert rows[1]["cost"] == 0.0                          # same picks, drift inside the band: no trades


# --- orders ---------------------------------------------------------------------------------


def test_plan_sells_first_then_buys_whole_shares_and_leaves_in_band_holdings():
    at = datetime(2026, 11, 30, 21, 30, tzinfo=UTC)
    prices = {"A": 100.0, "B": 50.0, "C": 333.0, "OLD": 20.0}
    held = {"A": 50, "OLD": 100}                   # A worth 5,000, about one slot: in band
    intents = plan_orders(["A", "B", "C"], held, prices, equity=15_000, n=3, band=0.25, tag="2026-11", at=at)
    slot = 15_000 * 0.995 / 3
    assert [(o.side, o.symbol, o.qty) for o in intents] == [
        ("sell", "OLD", 100), ("buy", "B", int(slot // 50)), ("buy", "C", int(slot // 333))]
    assert all(o.order_type == "market" and o.time_in_force == "day" for o in intents)
    assert intents[0].client_order_id == "us-m12-2026-11-OLD-sell"
    over = plan_orders(["A"], {"A": 200}, {"A": 100.0}, equity=10_000, n=1, band=0.25, tag="t", at=at)
    assert [(o.side, o.qty) for o in over] == [("sell", 200 - int(10_000 * 0.995 // 100))]


# --- when to book ----------------------------------------------------------------------------


def test_booking_window_is_after_the_month_end_close_until_the_next_open():
    assert booking_month(datetime(2026, 11, 30, 21, 30, tzinfo=UTC)) == "2026-11"   # 16:30 ET, last session
    assert booking_month(datetime(2026, 11, 30, 20, 0, tzinfo=UTC)) is None         # 15:00 ET, still open
    assert booking_month(datetime(2026, 12, 1, 13, 0, tzinfo=UTC)) == "2026-11"     # 08:00 ET next morning
    assert booking_month(datetime(2026, 12, 1, 15, 0, tzinfo=UTC)) is None          # 10:00 ET: too late
    assert booking_month(datetime(2026, 11, 27, 21, 30, tzinfo=UTC)) is None        # not the last session
    assert booking_month(datetime(2026, 12, 31, 21, 30, tzinfo=UTC)) == "2026-12"


# --- one booking, end to end on fakes -----------------------------------------------------------------


def test_book_sends_the_months_orders_once_and_records_them(tmp_path):
    fake = FakeAlpaca()
    cfg = AlpacaConfig(enabled=True, state_root=tmp_path)
    broker = AlpacaBroker(AlpacaAPI(cfg, "kid", "sec", transport=fake), cfg.state_dir)
    daily = closes({"A": 0.02, "B": 0.01, "C": 0.03, "SPY": 0.01, "RSP": 0.01, "SPMO": 0.01}, start="2025-09-30")
    daily.index = list(daily.index[:-1]) + [pd.Timestamp("2026-11-30")]

    class Data:
        def daily_closes(self, symbols, start, end):
            return daily[[s for s in symbols if s in daily]]

    at = datetime(2026, 11, 30, 21, 30, tzinfo=UTC)
    out = tmp_path / "bookings"
    rec = us_momentum.book(broker, Data(), ["A", "B", "C"], at, out, n=2, equity=100_000.0)
    assert rec["month"] == "2026-11" and rec["picks"] == ["C", "A"]
    assert rec["universe_size"] == 3 and rec["long_market_value"] is None   # equity given: no account read
    assert sorted(o["symbol"] for o in rec["orders"]) == ["A", "C"]
    assert len([c for c in fake.calls if c[:2] == ("POST", "/v2/orders")]) == 2
    assert json.loads((out / "2026-11.json").read_text())["picks"] == ["C", "A"]
    again = us_momentum.book(broker, Data(), ["A", "B", "C"], at, out, n=2, equity=100_000.0)
    assert again["skipped"] == "already booked"
    assert len([c for c in fake.calls if c[:2] == ("POST", "/v2/orders")]) == 2
    assert us_momentum.book(broker, Data(), ["A"], datetime(2026, 11, 27, 21, 30, tzinfo=UTC), out)["skipped"]
