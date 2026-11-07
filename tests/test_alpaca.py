"""The Alpaca paper adapter's own paths: flags, live refusal, idempotent retries,
fail-closed parsing, halts, shadow mode, reconciliation and the raw journal."""

import json
from datetime import UTC, datetime

import pytest
from alpaca_fake import FakeAlpaca

from swingbot.broker.alpaca import (
    PAPER_URL,
    AlpacaAPI,
    AlpacaBroker,
    AlpacaConfig,
    AlpacaError,
    AlpacaUnreachable,
)
from swingbot.markets import (
    ExecutionMode,
    Fill,
    Halted,
    OrderIntent,
    OrderStatus,
    SendRefused,
    halt,
)

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def buy(cid="us-r1-0001", qty=10, **kw):
    base = dict(market="us", client_order_id=cid, symbol="AAPL", side="buy", qty=qty, order_type="limit",
                limit_price=190.25, time_in_force="day", created_utc=T0)
    base.update(kw)
    return OrderIntent(**base)


def make(tmp_path, fake=None, **cfg):
    fake = fake or FakeAlpaca()
    config = AlpacaConfig(**{"enabled": True, "state_root": tmp_path, **cfg})
    return AlpacaBroker(AlpacaAPI(config, "kid", "sec", transport=fake), config.state_dir), fake


# --- what must stop anything being sent --------------------------------------------------


def test_keys_alone_send_nothing_the_adapter_must_be_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SWINGBOT_ALPACA_KEY_ID", "kid")
    monkeypatch.setenv("SWINGBOT_ALPACA_SECRET_KEY", "sec")
    fake = FakeAlpaca()
    with pytest.raises(AlpacaError, match="enabled"):
        AlpacaAPI.from_env(AlpacaConfig(state_root=tmp_path), transport=fake)
    assert fake.calls == []


def test_live_mode_is_refused_in_code(tmp_path):
    fake = FakeAlpaca()
    with pytest.raises(SendRefused, match="live"):
        AlpacaAPI(AlpacaConfig(enabled=True, mode=ExecutionMode.LIVE, state_root=tmp_path), "kid", "sec",
                  transport=fake)
    assert fake.calls == []


def test_missing_credentials_name_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGBOT_ALPACA_KEY_ID", raising=False)
    with pytest.raises(AlpacaError, match="SWINGBOT_ALPACA_KEY_ID"):
        AlpacaAPI.from_env(AlpacaConfig(enabled=True, state_root=tmp_path))


def test_config_loads_from_broker_toml_and_defaults_to_disabled_paper(tmp_path):
    assert AlpacaConfig.load(tmp_path / "missing.toml", required=False).enabled is False
    p = tmp_path / "broker.toml"
    p.write_text('[alpaca]\nenabled = true\nmode = "shadow"\n')
    c = AlpacaConfig.load(p)
    assert c.enabled and c.mode is ExecutionMode.SHADOW and c.base_url == PAPER_URL
    p.write_text('[alpaca]\nmode = "yolo"\n')
    with pytest.raises(ValueError):
        AlpacaConfig.load(p)


def test_a_halt_stops_submits_before_any_call(tmp_path):
    b, fake = make(tmp_path)
    halt("us", "broker outage", state_root=tmp_path)
    with pytest.raises(Halted, match="broker outage"):
        b.submit(buy())
    assert fake.calls == []


def test_shadow_mode_reads_the_account_but_never_sends(tmp_path):
    b, fake = make(tmp_path, mode=ExecutionMode.SHADOW)
    assert b.positions() == []
    with pytest.raises(SendRefused, match="shadow"):
        b.submit(buy())
    assert all(m == "GET" for m, _, _ in fake.calls)


# --- the order path ---------------------------------------------------------------------------


def test_submit_sends_alpacas_fields_and_keeps_the_raw_response(tmp_path):
    b, fake = make(tmp_path)
    up = b.submit(buy())
    method, path, body = fake.calls[-1]
    assert (method, path) == ("POST", "/v2/orders")
    assert body == {"symbol": "AAPL", "qty": "10", "side": "buy", "type": "limit", "time_in_force": "day",
                    "client_order_id": "us-r1-0001", "limit_price": "190.25"}
    assert up.broker_order_id == "a0001" and up.status is OrderStatus.ACCEPTED
    journal = [json.loads(line) for line in (tmp_path / "markets/us/alpaca_raw.jsonl").read_text().splitlines()]
    assert journal[-1]["path"] == "/v2/orders" and journal[-1]["status"] == 200
    assert json.loads(journal[-1]["body"])["id"] == "a0001"
    assert "sec" not in (tmp_path / "markets/us/alpaca_raw.jsonl").read_text()


def test_a_lost_response_after_alpaca_took_the_order_is_adopted_not_resent(tmp_path):
    b, fake = make(tmp_path)
    fake.lose_next_post = "after"
    up = b.submit(buy())
    assert up.broker_order_id == "a0001" and len(fake.orders) == 1
    assert [c[:2] for c in fake.calls] == [("POST", "/v2/orders"), ("GET", "/v2/orders:by_client_order_id")]


def test_a_lost_request_that_never_arrived_is_sent_once_more(tmp_path):
    b, fake = make(tmp_path)
    fake.lose_next_post = "before"
    up = b.submit(buy())
    assert up.status is OrderStatus.ACCEPTED and len(fake.orders) == 1
    assert [c[:2] for c in fake.calls].count(("POST", "/v2/orders")) == 2


def _lose_both_sends(b, fake):
    """Every POST is lost before it arrives: the order is saved as new, with no Alpaca id."""
    real = fake._post

    def lost(p):
        fake.lose_next_post = "before"
        return real(p)
    fake._post = lost
    with pytest.raises(AlpacaUnreachable):
        b.submit(buy())
    fake._post = real
    assert fake.orders == {}


def test_an_order_whose_sends_were_all_lost_is_sent_on_the_next_run(tmp_path):
    b, fake = make(tmp_path)
    _lose_both_sends(b, fake)
    up = AlpacaBroker(b.api, b.state_dir).submit(buy())       # the next run reads the saved book
    assert up.status is OrderStatus.ACCEPTED and up.broker_order_id == "a0001" and len(fake.orders) == 1


def test_a_retried_order_that_alpaca_already_has_is_adopted_not_rejected(tmp_path):
    b, fake = make(tmp_path)
    _lose_both_sends(b, fake)
    fake._post({"symbol": "AAPL", "qty": "10", "side": "buy", "type": "limit", "time_in_force": "day",
                "client_order_id": "us-r1-0001", "limit_price": "190.25"})   # it did arrive after all
    up = AlpacaBroker(b.api, b.state_dir).submit(buy())
    assert up.status is OrderStatus.ACCEPTED and up.broker_order_id == "a0001" and len(fake.orders) == 1


def test_a_refused_order_comes_back_rejected_with_alpacas_reason(tmp_path):
    b, fake = make(tmp_path)
    fake.reject_next_post = True
    up = b.submit(buy())
    assert up.status is OrderStatus.REJECTED and "buying power" in up.reason
    assert b.open_orders() == []


def test_the_book_survives_a_restart(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.execute("us-r1-0001", 10, 190.0)
    assert len([e for e in b.sync() if isinstance(e, Fill)]) == 1
    again = AlpacaBroker(b.api, b.state_dir)
    assert again.sync() == []                       # the fill is not reported twice after a restart
    assert again.submit(buy()).broker_order_id == "a0001"


def test_fills_are_read_across_pages(tmp_path):
    b, fake = make(tmp_path)
    b.page_size = 2
    b.submit(buy(qty=5))
    for _ in range(5):
        fake.execute("us-r1-0001", 1, 190.0)
    assert sum(e.qty for e in b.sync() if isinstance(e, Fill)) == 5


def test_close_sells_the_whole_holding_at_market(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.execute("us-r1-0001", 10, 190.0)
    b.sync()
    up = b.close("AAPL", T0)
    _, _, body = fake.calls[-1]
    assert (body["side"], body["type"], body["qty"]) == ("sell", "market", "10")
    assert up.client_order_id.startswith("us-close-AAPL-")
    with pytest.raises(Exception, match="no AAPL"):
        AlpacaBroker(b.api, tmp_path / "other").close("AAPL", T0)


# --- fail closed --------------------------------------------------------------------------


def test_a_malformed_order_response_fails_closed_and_leaves_the_book_alone(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.override_next = {"status": "filled"}              # no id, no client_order_id
    with pytest.raises(AlpacaError, match="malformed"):
        b.sync()
    assert b.open_orders()[0].status is OrderStatus.ACCEPTED


def test_an_unknown_status_fails_closed(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.orders["a0001"]["status"] = "teleported"
    with pytest.raises(AlpacaError, match="teleported"):
        b.sync()


def test_a_stale_response_that_goes_backwards_fails_closed(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.execute("us-r1-0001", 10, 190.0)
    b.sync()
    b._book["orders"]["us-r1-0001"]["status"] = "partially_filled"   # pretend we had not seen it final
    b._save()
    stale = dict(fake.orders["a0001"], status="accepted", filled_qty="0")
    fake.override_next = stale
    with pytest.raises(AlpacaError, match="backwards"):
        b.sync()


def test_a_fractional_fill_fails_closed(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.fill_qty_override = "0.5"
    fake.execute("us-r1-0001", 1, 190.0)
    with pytest.raises(AlpacaError, match="whole"):
        b.sync()


# --- reconciliation ------------------------------------------------------------------------


def test_reconcile_compares_alpacas_positions_with_the_fills_in_the_book(tmp_path):
    b, fake = make(tmp_path)
    b.submit(buy())
    fake.execute("us-r1-0001", 10, 190.0)
    b.sync()
    assert b.reconcile() == []
    fake.positions["AAPL"]["qty"] = "7"
    fake.positions["MSFT"] = {"symbol": "MSFT", "qty": "3", "avg_entry_price": "400"}
    assert b.reconcile() == ["AAPL: Alpaca holds 7, the book's fills say 10",
                             "MSFT: Alpaca holds 3, the book's fills say 0"]
