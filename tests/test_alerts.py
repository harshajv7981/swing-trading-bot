import json

import pytest

from swingbot.alerts import URGENT, AlertConfig, collect, dispatch
from swingbot.daily.cli import main as daily_main


def write_state(tmp_path, fills=(), orders=(), kill=None):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in [{"kind": "open", "cash": 500000}, *fills]))
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "paper_broker.json").write_text(json.dumps({"orders": list(orders)}))
    if kill:
        (state / "kill_switch.json").write_text(json.dumps(kill))
    return ledger, state


BUY = {"kind": "fill", "order_id": "P00001", "at": "2026-11-04 09:15", "ticker": "ABC", "side": "buy",
       "qty": 10, "price": 101.0, "reason": "entry", "stop": 95.0, "target": 115.0}
GAP = {"kind": "fill", "order_id": "P00002", "at": "2026-11-06 09:15", "ticker": "ABC", "side": "sell",
       "qty": 10, "price": 90.0, "reason": "stop-gap"}
LAPSED = {"id": "P00003", "type": "oco", "status": "open", "ticker": "XYZ", "qty": 5, "stop_lapsed": "2026-11-06"}
ENTRY = {"id": "P00004", "type": "entry", "status": "open", "ticker": "DEF", "qty": 3, "limit": 50.0,
         "session": "2026-11-07", "stop": 47.0, "target": 56.0}


def test_collect_reads_fills_lapsed_stops_orders_the_latch_and_failures(tmp_path):
    ledger, state = write_state(tmp_path, [BUY, GAP], [LAPSED, ENTRY],
                                {"latched": True, "reason": "equity 3% under the week's open", "at": "x"})
    steps = [{"name": "refresh", "status": "failed", "detail": "no network"},
             {"name": "calendar", "status": "ok", "detail": ""}]
    alerts = collect(ledger, state, AlertConfig(), "2026-11-06", steps)
    titles = [a.title for a in alerts]
    assert titles == ["Bought ABC", "Stop gapped through: ABC", "NO STOP on XYZ", "Paper order: DEF",
                      "KILL SWITCH LATCHED", "Daily loop: refresh failed"]
    assert [a.level for a in alerts].count(URGENT) == 3
    assert "stop 95.00, target 115.00" in alerts[0].text
    assert collect(ledger, state, AlertConfig(fills=False, orders=False, failures=False), "", steps)[0].title \
        == "NO STOP on XYZ"


def test_first_run_sends_only_the_welcome_then_each_alert_once(tmp_path):
    ledger, state = write_state(tmp_path, [BUY])
    got = []
    d = dispatch(ledger, state, AlertConfig(channel="ntfy"), send=got.append)
    assert d.seeded == 1 and [a.key for a in got] == ["hello"]
    assert dispatch(ledger, state, AlertConfig(channel="ntfy"), send=got.append).sent == []

    ledger.write_text(ledger.read_text() + json.dumps(GAP) + "\n")
    dispatch(ledger, state, AlertConfig(channel="ntfy"), send=got.append)
    dispatch(ledger, state, AlertConfig(channel="ntfy"), send=got.append)
    assert [a.title for a in got] == ["Swing bot alerts are on", "Stop gapped through: ABC"]
    assert len((state / "alerts.jsonl").read_text().splitlines()) == 2


def test_an_alert_that_fails_to_send_is_tried_again(tmp_path):
    ledger, state = write_state(tmp_path)
    dispatch(ledger, state, AlertConfig(channel="ntfy"), send=lambda a: None)
    ledger.write_text(ledger.read_text() + json.dumps(BUY) + "\n")

    def down(a):
        raise OSError("no network")
    d = dispatch(ledger, state, AlertConfig(channel="ntfy"), send=down)
    assert d.sent == [] and "no network" in d.failed[0][1]
    got = []
    dispatch(ledger, state, AlertConfig(channel="ntfy"), send=got.append)
    assert [a.title for a in got] == ["Bought ABC"]


def test_a_missing_secret_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.delenv("SWINGBOT_TELEGRAM_TOKEN", raising=False)
    ledger, state = write_state(tmp_path)
    d = dispatch(ledger, state, AlertConfig(channel="telegram"))
    assert "SWINGBOT_TELEGRAM_TOKEN is not set" in d.failed[0][1]


def test_config_rejects_an_unknown_channel():
    with pytest.raises(ValueError, match="channel"):
        AlertConfig.from_toml({"channel": "sms"})
    assert AlertConfig.from_toml({"channel": "Telegram", "orders": False}) == AlertConfig("telegram", orders=False)


def test_test_message_needs_a_channel(tmp_path, capsys):
    cfg = tmp_path / "daily.toml"
    cfg.write_text('[alerts]\nchannel = "none"\n')
    assert daily_main(["--config", str(cfg), "alerts", "--test"]) == 2
    assert "channel is \"none\"" in capsys.readouterr().err
