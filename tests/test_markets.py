"""Phase 0 of docs/parallel-markets-learning-plan.md: market profiles, run
namespaces, modes and halt files, and the normalized order contracts."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from swingbot.markets import (
    ExecutionMode,
    Fill,
    Halted,
    OrderIntent,
    OrderStatus,
    Position,
    RunSpace,
    SendRefused,
    get_profile,
    halt,
    halted,
    may_send,
    resume,
)
from swingbot.markets.contracts import ContractError



# --- profiles -------------------------------------------------------------------


def test_the_two_profiles_carry_their_own_market_rules():
    nse, us = get_profile("nse"), get_profile("us_equities")
    assert (nse.key, nse.name, nse.currency, nse.timezone) == ("nse", "nse_equities", "INR", ZoneInfo("Asia/Kolkata"))
    assert (us.key, us.name, us.currency, us.timezone) == ("us", "us_equities", "USD", ZoneInfo("America/New_York"))
    assert (nse.broker, us.broker) == ("dhan", "alpaca")
    assert (us.session_open, us.session_close, us.early_close) == (time(9, 30), time(16, 0), time(13, 0))
    assert (nse.session_open, nse.session_close) == (time(9, 15), time(15, 30))
    with pytest.raises(KeyError):
        get_profile("lse")


def test_ticks_follow_each_market():
    nse, us = get_profile("nse"), get_profile("us")
    assert nse.tick(100.0) == 0.01 and nse.tick(300.0) == 0.05   # NSE's price-banded tick, reused from risk.exits
    assert us.tick(12.34) == 0.01 and us.tick(0.5) == 0.0001     # Reg NMS sub-penny rule
    assert us.on_tick(12.34) and not us.on_tick(12.345)
    assert us.on_tick(0.1234) and not us.on_tick(0.12345)


def test_us_calendar_knows_nyse_holidays_and_early_closes():
    cal = get_profile("us").calendar()
    assert not cal.is_session(date(2026, 11, 26))             # Thanksgiving
    assert cal.is_session(date(2026, 11, 27))
    assert cal.close_on(date(2026, 11, 27)) == time(13, 0)    # day after Thanksgiving
    assert cal.close_on(date(2026, 11, 30)) == time(16, 0)
    assert not cal.is_session(date(2027, 7, 5))               # Independence Day observed
    assert not cal.is_session(date(2026, 11, 28))             # Saturday
    assert cal.knows(2027) and not cal.knows(2028)


def test_session_checks_use_market_time_never_the_host_clock():
    us, nse = get_profile("us"), get_profile("nse")
    # 15:00 UTC on 2 Nov 2026 is 10:00 in New York (EST) and 20:30 in Mumbai.
    t = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    assert us.in_session(t) and not nse.in_session(t)
    assert us.local(t).hour == 10
    # Early close: 13:30 New York on 27 Nov is after the bell.
    assert not us.in_session(datetime(2026, 11, 27, 18, 30, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        us.in_session(datetime(2026, 11, 2, 15, 0))


# --- run namespaces -------------------------------------------------------------


def test_runs_live_in_separate_market_namespaces(tmp_path):
    nse = RunSpace.create("nse", "2026-11-07-a", runs_root=tmp_path / "runs", state_root=tmp_path / "state",
                          config={"strategy": "m6"})
    us = RunSpace.create("us", "2026-11-07-a", runs_root=tmp_path / "runs", state_root=tmp_path / "state")
    assert nse.run_dir == tmp_path / "runs/markets/nse/2026-11-07-a"
    assert us.run_dir == tmp_path / "runs/markets/us/2026-11-07-a"
    assert nse.state_dir == tmp_path / "state/markets/nse" and us.state_dir == tmp_path / "state/markets/us"
    manifest = nse.manifest()
    assert manifest["market"] == "nse" and manifest["profile"] == "nse_equities" and manifest["currency"] == "INR"
    assert manifest["config"] == {"strategy": "m6"} and manifest["created_utc"].endswith("Z")
    assert us.manifest()["config"] == {}


def test_a_run_id_is_created_once_and_its_config_stays_frozen(tmp_path):
    kw = dict(runs_root=tmp_path / "runs", state_root=tmp_path / "state")
    RunSpace.create("us", "r1", **kw)
    with pytest.raises(FileExistsError):
        RunSpace.create("us", "r1", **kw)
    assert RunSpace.open("us", "r1", **kw).manifest()["market"] == "us"
    with pytest.raises(ValueError):
        RunSpace.create("us", "../nse/r1", **kw)


# --- modes and halt files ----------------------------------------------------------


def test_paper_may_send_shadow_and_live_may_not(tmp_path):
    assert may_send("us", ExecutionMode.PAPER, state_root=tmp_path) is None
    with pytest.raises(SendRefused, match="shadow"):
        may_send("us", ExecutionMode.SHADOW, state_root=tmp_path)
    with pytest.raises(SendRefused, match="live"):
        may_send("nse", ExecutionMode.LIVE, state_root=tmp_path)
    with pytest.raises(ValueError):
        ExecutionMode.parse("yolo")
    assert ExecutionMode.parse("Paper") is ExecutionMode.PAPER


def test_a_market_halt_stops_only_that_market_and_the_global_halt_stops_both(tmp_path):
    halt("us", "alpaca outage", state_root=tmp_path)
    assert halted("us", state_root=tmp_path) and not halted("nse", state_root=tmp_path)
    with pytest.raises(Halted, match="alpaca outage"):
        may_send("us", ExecutionMode.PAPER, state_root=tmp_path)
    may_send("nse", ExecutionMode.PAPER, state_root=tmp_path)
    halt(None, "all stop", state_root=tmp_path)
    with pytest.raises(Halted, match="all stop"):
        may_send("nse", ExecutionMode.PAPER, state_root=tmp_path)
    resume(None, state_root=tmp_path)
    resume("us", state_root=tmp_path)
    assert not halted("us", state_root=tmp_path) and not halted("nse", state_root=tmp_path)


# --- contracts ------------------------------------------------------------------------


def intent(**kw):
    base = dict(market="us", client_order_id="us-r1-0001", symbol="AAPL", side="buy", qty=10,
                order_type="limit", limit_price=190.25, time_in_force="day",
                created_utc=datetime(2026, 11, 2, 15, 0, tzinfo=UTC), strategy="momentum", strategy_version="1")
    base.update(kw)
    return OrderIntent(**base)


def test_an_order_intent_is_validated_against_its_market_profile():
    o = intent()
    assert o.profile.currency == "USD" and o.notional == pytest.approx(1902.5)
    for bad, msg in [(dict(qty=0), "quantity"), (dict(qty=1.5), "whole"), (dict(side="short"), "side"),
                     (dict(limit_price=190.255), "tick"), (dict(limit_price=None), "limit price"),
                     (dict(order_type="bracket"), "order type"), (dict(time_in_force="gtx"), "time in force"),
                     (dict(created_utc=datetime(2026, 11, 2, 15, 0)), "timezone"), (dict(symbol=""), "symbol"),
                     (dict(client_order_id=""), "client order id"), (dict(market="lse"), "market"),
                     (dict(order_type="stop", limit_price=None, stop_price=None), "stop price")]:
        with pytest.raises(ContractError, match=msg):
            intent(**bad)
    nse = intent(market="nse", symbol="TCS", limit_price=3001.1)   # NSE's tick is 0.10 above ₹1,000
    assert nse.profile.currency == "INR"
    with pytest.raises(ContractError, match="tick"):
        intent(market="nse", symbol="TCS", limit_price=3001.05)
    with pytest.raises(ContractError, match="time in force"):
        intent(market="nse", symbol="TCS", limit_price=3001.1, time_in_force="gtc")


def test_an_intent_round_trips_through_a_record():
    o = intent()
    rec = o.to_record()
    assert rec["created_utc"] == "2026-11-02T15:00:00Z" and rec["kind"] == "order_intent"
    assert OrderIntent.from_record(rec) == o


def test_order_status_knows_which_states_are_final():
    assert OrderStatus.FILLED.final and OrderStatus.REJECTED.final and OrderStatus.EXPIRED.final
    assert not OrderStatus.ACCEPTED.final and not OrderStatus.PARTIALLY_FILLED.final


def test_fills_and_positions_carry_currency_and_utc_time():
    f = Fill(market="us", fill_id="a1", client_order_id="us-r1-0001", broker_order_id="b1", symbol="AAPL",
             side="buy", qty=4, price=190.2, fees=0.0, at_utc=datetime(2026, 11, 2, 15, 1, tzinfo=UTC))
    assert f.currency == "USD" and f.notional == pytest.approx(760.8)
    assert f.to_record()["at_utc"] == "2026-11-02T15:01:00Z"
    with pytest.raises(ContractError):
        Fill(market="us", fill_id="", client_order_id="x", broker_order_id="b", symbol="AAPL", side="buy",
             qty=1, price=1.0, fees=0.0, at_utc=datetime(2026, 11, 2, tzinfo=UTC))
    p = Position(market="us", symbol="AAPL", qty=4, avg_price=190.2)
    assert p.currency == "USD" and p.cost == pytest.approx(760.8)
