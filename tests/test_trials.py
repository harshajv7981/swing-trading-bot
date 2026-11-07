"""Phase 3: one monthly record for the NSE and US paper trials, and a report that
compares them only in returns, never in rupees against dollars."""

import json

import pytest

from swingbot.markets.trials import from_nse_ledger, from_us_state, report

NSE = {"start": "2026-10-30", "capital": 1_000_000, "months": [
    {"date": "2026-10-30", "value": 1_000_000, "cash": 20_000, "costs": 2_000, "turnover": 0.5,
     "picks": ["A", "B"], "bucket_size": 150, "orders": [{"symbol": "A"}, {"symbol": "B"}], "notes": [],
     "indexes": {"Nifty Midcap 150": 100.0}, "ew": {"level": 0.998}, "holdings": {}},
    {"date": "2026-11-30", "value": 1_100_000, "cash": 10_000, "costs": 500, "turnover": 0.1,
     "picks": ["A", "C"], "bucket_size": 150, "orders": [{"symbol": "B"}, {"symbol": "C"}],
     "notes": ["C: no close on 2026-11-30; bought at the last one"],
     "indexes": {"Nifty Midcap 150": 105.0}, "ew": {"level": 1.05}, "holdings": {}},
    {"date": "2026-12-31", "value": 990_000, "cash": 10_000, "costs": 300, "turnover": 0.05,
     "picks": ["A", "C"], "bucket_size": 150, "orders": [], "notes": [],
     "indexes": {"Nifty Midcap 150": 99.75}, "ew": {"level": 1.0}, "holdings": {}},
]}


def test_nse_months_come_from_the_trial_ledger():
    rows = from_nse_ledger(NSE)
    assert [r.month for r in rows] == ["2026-10", "2026-11", "2026-12"]
    first, second, third = rows
    assert (first.currency, first.strategy, first.universe_size) == ("INR", "nse-m6", 150)
    assert first.month_return == pytest.approx(0.0) and second.month_return == pytest.approx(0.10)
    assert third.since_start == pytest.approx(-0.01)
    assert third.bench["Nifty Midcap 150"] == pytest.approx(-0.0025)
    assert third.bench["Mid-cap EW"] == pytest.approx(0.0)
    assert third.drawdown == pytest.approx(990_000 / 1_100_000 - 1)
    assert first.exposure == pytest.approx(0.98)
    assert second.orders == (2, 2, 0) and second.anomalies == ["C: no close on 2026-11-30; bought at the last one"]
    assert first.info_cutoff_utc == "2026-10-30T10:00:00Z"          # 15:30 IST


def booking(month, asof, equity, orders, bench, **kw):
    return {"month": month, "asof": asof, "equity": equity, "picks": ["X", "Y"], "orders": orders,
            "bench": bench, "reconcile": [], "notes": [], **kw}


def test_us_months_come_from_bookings_and_the_alpaca_book(tmp_path):
    us = tmp_path / "markets/us"
    (us / "us-m12/bookings").mkdir(parents=True)
    b1 = booking("2026-11", "2026-11-30", 100_000.0,
                 [{"client_order_id": "us-m12-2026-11-X-buy", "status": "accepted"},
                  {"client_order_id": "us-m12-2026-11-Y-buy", "status": "accepted"}],
                 {"RSP": 200.0, "SPY": 600.0, "SPMO": 100.0}, universe_size=503, long_market_value=0.0)
    b2 = booking("2026-12", "2026-12-31", 103_000.0,
                 [{"client_order_id": "us-m12-2026-12-Z-buy", "status": "rejected"}],
                 {"RSP": 202.0, "SPY": 612.0, "SPMO": 103.0}, universe_size=503, long_market_value=99_000.0,
                 reconcile=["Y: Alpaca holds 0, the book's fills say 5"])
    for b in (b1, b2):
        (us / f"us-m12/bookings/{b['month']}.json").write_text(json.dumps(b))
    (us / "alpaca_book.json").write_text(json.dumps({"fills": [
        {"client_order_id": "us-m12-2026-11-X-buy", "fill_id": "f1"},
        {"client_order_id": "us-m12-2026-11-X-buy", "fill_id": "f2"}], "orders": {}, "fills_seen": []}))
    first, second = from_us_state(tmp_path)
    assert (first.currency, first.strategy, first.info_cutoff_utc) == ("USD", "us-m12", "2026-11-30T21:00:00Z")
    assert first.orders == (2, 1, 0)                    # Y never filled
    assert second.orders == (1, 0, 1)
    assert second.month_return == pytest.approx(0.03) and second.bench["RSP"] == pytest.approx(0.01)
    assert second.exposure == pytest.approx(99_000 / 103_000) and first.exposure == pytest.approx(0.0)
    assert "us-m12-2026-11-Y-buy was not filled" in " ".join(first.anomalies)
    assert any("Alpaca holds 0" in a for a in second.anomalies)
    assert any("rejected" in a for a in second.anomalies)


def test_report_keeps_markets_apart_and_compares_only_returns():
    text = report(from_nse_ledger(NSE), [])
    assert "## NSE (INR)" in text and "## US (USD)" in text and "No US month booked yet" in text
    side = text.split("## Side by side")[1]
    assert "Rs" not in side and "$" not in side and "%" in side
    assert "| 2026-11 | +10.0% |" in side
