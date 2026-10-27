import json

import pytest
from test_broker import ticket

from swingbot.broker import OrderRefused
from swingbot.broker.dhan import (
    LIVE_URL,
    SANDBOX_URL,
    DhanAPI,
    DhanBroker,
    DhanConfig,
    DhanError,
    RateLimit,
    parse_scrip_master,
)
from swingbot.broker.dhan_cli import main as dhan_main
from swingbot.risk import NSECalendar, RiskConfig

SCRIPS = {"ABC": "1111", "DEF": "2222"}


class FakeDhan:
    """Enough of Dhan's v2 API to drive the adapter: orders, Forever Orders and trades."""

    def __init__(self):
        self.calls, self.trades, self.forever, self.n = [], [], {}, 0
        self.forever_status = "PENDING"
        self.swap_legs = False

    def __call__(self, method, url, headers, body):
        path = url.split("/v2", 1)[1]
        payload = json.loads(body) if body else None
        self.calls.append((method, path, payload))
        assert headers["access-token"] == "tok" and headers["client-id"] == "cid"
        if method == "POST" and path == "/orders":
            self.n += 1
            return 200, json.dumps({"orderId": f"O{self.n}", "orderStatus": "TRANSIT"}).encode()
        if method == "POST" and path == "/forever/orders":
            self.n += 1
            fid = f"F{self.n}"
            stop, target = payload["triggerPrice1"], payload["triggerPrice"]
            if self.swap_legs:
                stop, target = target, stop
            self.forever[fid] = [
                {"orderId": fid, "legName": "TARGET_LEG", "orderStatus": self.forever_status, "triggerPrice": target},
                {"orderId": fid, "legName": "STOP_LOSS_LEG", "orderStatus": self.forever_status,
                 "triggerPrice": stop}]
            return 200, json.dumps({"orderId": fid, "orderStatus": "PENDING"}).encode()
        if method == "GET" and path == "/forever/orders":
            return 200, json.dumps([leg for legs in self.forever.values() for leg in legs]).encode()
        if method == "DELETE" and path.startswith("/forever/orders/"):
            for leg in self.forever.get(path.rsplit("/", 1)[1], []):
                leg["orderStatus"] = "CANCELLED"
            return 200, b"{}"
        if method == "PUT" and path.startswith("/forever/orders/"):
            for leg in self.forever[path.rsplit("/", 1)[1]]:
                if leg["legName"] == payload["legName"]:
                    leg["triggerPrice"] = payload["triggerPrice"]
            return 200, b"{}"
        if method == "GET" and path.startswith("/trades/"):
            page = int(path.rsplit("/", 1)[1])
            return 200, json.dumps(self.trades if page == 0 else []).encode()
        if method == "GET" and path == "/trades":
            return 200, b"[]"
        if method == "GET" and path == "/fundlimit":
            return 200, json.dumps({"availabelBalance": 1000000.0}).encode()
        return 400, json.dumps({"errorCode": "DH-905", "errorMessage": "Invalid IP"}).encode()

    def trade(self, order_id, side, qty, price, at, security="1111"):
        self.trades.append({"orderId": order_id, "exchangeTradeId": f"T{len(self.trades)}", "transactionType": side,
                            "securityId": security, "tradedQuantity": qty, "tradedPrice": price,
                            "exchangeTime": at, "brokerageCharges": 0.0, "stt": 10.0, "stampDuty": 1.5})


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(tmp_path, fake=None, clock=None, **cfg):
    fake = fake or FakeDhan()
    clock = clock or Clock()
    config = DhanConfig(state_dir=tmp_path, **cfg)
    api = DhanAPI(config, "cid", "tok", transport=fake, limit=RateLimit(None, clock, clock.sleep))
    return DhanBroker(api, RiskConfig(), NSECalendar(), SCRIPS, clock, clock.sleep), fake, clock


def test_a_day_at_dhan_entry_fill_stop_confirmed_then_stopped_out(tmp_path):
    b, fake, _ = make(tmp_path)
    oid = b.submit(ticket(), "2026-01-02 18:00")      # Friday evening: an after-market order for Monday
    method, path, body = fake.calls[-1]
    assert (method, path) == ("POST", "/orders")
    assert body["securityId"] == "1111" and body["afterMarketOrder"] and body["amoTime"] == "OPEN"
    assert (body["productType"], body["orderType"], body["price"], body["quantity"]) == ("CNC", "LIMIT", 100.0, 100)
    assert body["dhanClientId"] == "cid"

    fake.trade("O1", "BUY", 100, 99.9, "2026-01-05 09:15:03")
    [buy] = b.sync("2026-01-05 18:00")                # the fill, but no Forever Order on day one
    assert (buy.order_id, buy.side, buy.price, buy.fingerprint, buy.costs) == (oid, "buy", 99.9, "f" * 16, 11.5)
    assert not any(p == "/forever/orders" and m == "POST" for m, p, _ in fake.calls)

    assert b.sync("2026-01-06 08:30") == [] and "after 09:15" in b.notes[-1]
    b.sync("2026-01-06 09:20")
    [(_, _, oco)] = [c for c in fake.calls if c[:2] == ("POST", "/forever/orders")]
    assert (oco["orderFlag"], oco["triggerPrice1"], oco["triggerPrice"], oco["quantity"]) == ("OCO", 95.0, 110.0, 100)
    assert b.orders[0]["forever_id"] == "F2" and "confirmed at Dhan (R1)" in b.notes[-1]

    fake.trade("S9", "SELL", 100, 94.8, "2026-01-08 11:02:00")
    [sell] = b.sync("2026-01-08 18:00")
    assert (sell.side, sell.qty, sell.reason) == ("sell", 100, "stop")
    assert b.orders[0]["status"] == "closed" and b.open_orders() == []
    assert b.sync("2026-01-09 18:00") == []           # each trade once


def test_r1_a_stop_dhan_does_not_confirm_in_60_seconds_is_retried_then_the_position_sold(tmp_path):
    fake = FakeDhan()
    fake.forever_status = "TRANSIT"   # never accepted: never confirmed
    b, fake, clock = make(tmp_path, fake)
    b.submit(ticket(), "2026-01-02 18:00")
    fake.trade("O1", "BUY", 100, 100.0, "2026-01-05 09:15:03")
    b.sync("2026-01-05 18:00")
    start = clock.t
    b.sync("2026-01-06 09:20")
    assert clock.t - start >= 120                      # two tries of 60 seconds
    placed = [c for c in fake.calls if c[:2] == ("POST", "/forever/orders")]
    assert len(placed) == 2
    sells = [c[2] for c in fake.calls if c[:2] == ("POST", "/orders") and c[2]["transactionType"] == "SELL"]
    assert len(sells) == 1 and sells[0]["orderType"] == "MARKET" and sells[0]["quantity"] == 100
    assert "NO confirmed stop" in b.notes[-1]


def test_r1_a_stop_at_the_wrong_price_is_not_a_stop(tmp_path):
    fake = FakeDhan()
    fake.swap_legs = True
    b, fake, _ = make(tmp_path, fake, flatten_unprotected=False)
    b.submit(ticket(), "2026-01-02 18:00")
    fake.trade("O1", "BUY", 100, 100.0, "2026-01-05 09:15:03")
    b.sync("2026-01-05 18:00")
    b.sync("2026-01-06 09:20")
    assert any("triggers at 110.00, not 95.00" in n for n in b.notes)
    assert "sell it or place the stop by hand" in b.notes[-1]
    assert all(leg["orderStatus"] == "CANCELLED" for legs in fake.forever.values() for leg in legs)


def test_a_stop_cancelled_at_dhan_is_placed_again(tmp_path):
    b, fake, _ = make(tmp_path)
    b.submit(ticket(), "2026-01-02 18:00")
    fake.trade("O1", "BUY", 100, 100.0, "2026-01-05 09:15:03")
    b.sync("2026-01-05 18:00")
    b.sync("2026-01-06 09:20")
    for leg in fake.forever["F2"]:
        leg["orderStatus"] = "CANCELLED"
    b.sync("2026-01-07 09:20")
    assert any("CANCELLED and the shares are still held" in n for n in b.notes)
    assert b.orders[0]["forever_id"] == "F3"


def test_an_unfilled_entry_lapses_and_modify_stop_only_goes_up(tmp_path):
    b, fake, _ = make(tmp_path)
    b.submit(ticket(), "2026-01-02 18:00")
    b.submit(ticket("DEF"), "2026-01-02 18:00")
    fake.trade("O1", "BUY", 100, 100.0, "2026-01-05 09:15:03")
    b.sync("2026-01-06 09:20")
    assert [o["status"] for o in b.orders] == ["held", "expired"]
    with pytest.raises(OrderRefused, match="only moves up"):
        b.modify_stop("ABC", 90.0, "2026-01-06 10:00")
    move = b.modify_stop("ABC", 98.0, "2026-01-06 10:00", "R7 trail")
    assert move.stop == 98.0 and fake.forever["F3"][1]["triggerPrice"] == 98.0
    b.close("ABC", "2026-01-06 10:05")
    assert fake.forever["F3"][0]["orderStatus"] == "CANCELLED"
    fake.trade("O4", "SELL", 100, 101.0, "2026-01-06 10:05:01")
    [sell] = b.sync("2026-01-06 18:00")
    assert sell.reason == "close" and b.open_orders() == []


def test_submit_refuses_what_the_paper_broker_refuses(tmp_path):
    b, _, _ = make(tmp_path)
    with pytest.raises(OrderRefused, match="kill switch"):
        b.submit(ticket(), "2026-01-02 18:00", kill_switch_latched=True)
    with pytest.raises(OrderRefused, match="scrip master"):
        b.submit(ticket("XYZ"), "2026-01-02 18:00")
    with pytest.raises(OrderRefused, match="PASS is from"):
        b.submit(ticket(checked="2025-12-20 18:00"), "2026-01-02 18:00")
    b.submit(ticket(), "2026-01-02 18:00")
    with pytest.raises(OrderRefused, match="does not add"):
        b.submit(ticket(), "2026-01-02 18:05")


def test_r10_waits_out_a_busy_second_and_refuses_past_the_hour(tmp_path):
    clock = Clock()
    limit = RateLimit(tmp_path / "rate.json", clock, clock.sleep)
    for _ in range(10):
        limit.take("order")
    start = clock.t
    limit.take("order")                                 # the 11th in one second waits
    assert clock.t - start == pytest.approx(1.0)
    assert len(RateLimit(tmp_path / "rate.json", clock, clock.sleep).calls["order"]) == 11   # persisted
    limit.calls["order"] = [clock.t - 100 + i * 0.09 for i in range(1000)]
    with pytest.raises(DhanError, match="R10"):
        limit.take("order")


def test_r11_live_needs_the_config_and_the_environment_and_is_read_only(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGBOT_DHAN_LIVE", raising=False)
    assert DhanConfig().base_url == SANDBOX_URL
    with pytest.raises(DhanError, match="SWINGBOT_DHAN_LIVE"):
        _ = DhanConfig(mode="live").base_url
    monkeypatch.setenv("SWINGBOT_DHAN_LIVE", "yes")
    live = DhanConfig(mode="live", state_dir=tmp_path)
    assert live.base_url == LIVE_URL
    api = DhanAPI(live, "cid", "tok", transport=FakeDhan(), limit=RateLimit(None))
    assert api.call("GET", "/fundlimit")["availabelBalance"] == 1000000.0
    with pytest.raises(DhanError, match="read-only"):
        api.call("POST", "/orders", {})


def test_dhan_errors_are_plain_sentences(tmp_path):
    api = DhanAPI(DhanConfig(state_dir=tmp_path), "cid", "tok", transport=FakeDhan(), limit=RateLimit(None))
    with pytest.raises(DhanError, match="static IP"):
        api.call("GET", "/nowhere")


def test_scrip_master_keeps_nse_equities_only(tmp_path, capsys):
    text = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES\n"
            "NSE,E,2885,EQUITY,RELIANCE,EQ\nBSE,E,500325,EQUITY,RELIANCE,A\nNSE,D,35001,FUTSTK,RELIANCE-NOV,\n"
            "NSE,E,9999,EQUITY,SOMEBOND,N1\n")
    assert parse_scrip_master(text) == {"RELIANCE": "2885"}
    f = tmp_path / "master.csv"
    f.write_text(text)
    cfg = tmp_path / "broker.toml"
    cfg.write_text(f'[dhan]\nstate_dir = "{tmp_path}"\nscrip_master = "{tmp_path / "s.csv"}"\n')
    assert dhan_main(["--config", str(cfg), "scrips", "--file", str(f)]) == 2   # too few rows to be real
    assert "only 1 NSE equities" in capsys.readouterr().err


def test_time_exit_drops_the_oco_and_sells_at_market_from_1515(tmp_path):
    b, fake, _ = make(tmp_path)
    t = {**ticket(), "hold": {"max_sessions": 3, "entry_session": "2026-01-05", "exit_by": "2026-01-07"}}
    oid = b.submit(t, "2026-01-02 18:00")
    fake.trade("O1", "BUY", 100, 100.0, "2026-01-05 09:15:03")
    b.sync("2026-01-05 18:00")
    b.sync("2026-01-06 09:20")                         # the Forever OCO goes in
    fid = b.orders[0]["forever_id"]
    b.sync("2026-01-07 11:00")                         # exit_by, but before 15:15: still held
    assert not any(c[0] == "POST" and c[1] == "/orders" and c[2]["transactionType"] == "SELL" for c in fake.calls)
    b.sync("2026-01-07 15:16")
    [(_, _, sell)] = [c for c in fake.calls if c[:2] == ("POST", "/orders") and c[2]["transactionType"] == "SELL"]
    assert (sell["orderType"], sell["quantity"]) == ("MARKET", 100)
    assert ("DELETE", f"/forever/orders/{fid}", None) in fake.calls
    exit_id = b.orders[0]["exit_id"]
    fake.trade(exit_id, "SELL", 100, 103.0, "2026-01-07 15:16:30")
    [fill] = b.sync("2026-01-07 18:00")
    assert (fill.order_id, fill.reason, fill.price) == (oid, "time", 103.0)
    assert b.open_orders() == []


def test_a_missed_time_exit_waits_for_market_hours(tmp_path):
    b, fake, _ = make(tmp_path)
    b.submit({**ticket(), "hold": {"exit_by": "2026-01-06"}}, "2026-01-02 18:00")
    fake.trade("O1", "BUY", 100, 100.0, "2026-01-05 09:15:03")
    b.sync("2026-01-05 18:00")
    b.sync("2026-01-07 08:30")                         # past exit_by, but the market is shut
    assert any("past its time exit" in n for n in b.notes) and not b.orders[0].get("exit_id")
    b.sync("2026-01-07 09:20")
    assert b.orders[0]["exit_reason"] == "time"
