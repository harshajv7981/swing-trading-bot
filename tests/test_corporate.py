from datetime import date

import pandas as pd
import pytest
from test_broker import held, ohlc, ticket

from swingbot import corporate
from swingbot.broker import PaperBroker, record_fills
from swingbot.paper import Ledger, replay
from swingbot.risk import RiskConfig

SESSIONS = pd.bdate_range("2026-01-05", periods=10)
BONUS = corporate.Action("ABC", date(2026, 1, 12), 2.0, "refresh", "2026-01-13 08:00")


def write(tmp_path, *actions):
    for a in actions:
        corporate.add(tmp_path, a)
    return corporate.load(tmp_path)


def test_split_ratio_tells_a_bonus_from_a_dividend():
    assert corporate.split_ratio(2.004) == 2.0
    assert corporate.split_ratio(1.4995) == 1.5
    assert corporate.split_ratio(10.02) == 10.0
    assert corporate.split_ratio(1.012) is None          # a dividend adjustment
    assert corporate.split_ratio(2.7) is None            # no simple ratio: not a split


def test_actions_file_round_trip_and_no_duplicates(tmp_path):
    assert corporate.add(tmp_path, BONUS) and not corporate.add(tmp_path, BONUS)
    assert corporate.load(tmp_path) == {"ABC": [BONUS]}
    assert corporate.load(tmp_path / "none") == {}
    assert corporate.factor({"ABC": [BONUS]}, "abc", date(2026, 1, 9)) == 2.0
    assert corporate.factor({"ABC": [BONUS]}, "ABC", date(2026, 1, 12)) == 1.0


def test_a_bonus_after_a_closed_trade_no_longer_latches_the_kill_switch(tmp_path):
    # Bought at 1,000 and sold at 1,010; a later 1:1 bonus halved the saved history.
    bars = {"ABC": ohlc(*[(500.0, 505, 495, 500)] * 10)}
    recs = [{"kind": "open", "at": "2026-01-02 18:00", "cash": 1_000_000},
            {"kind": "fill", "at": "2026-01-05 09:15", "ticker": "ABC", "side": "buy", "qty": 150, "price": 1000.0,
             "stop": 950.0, "target": 1100.0, "verdict": "PASS", "sector": "X", "costs": 0},
            {"kind": "fill", "at": "2026-01-07 15:30", "ticker": "ABC", "side": "sell", "qty": 150, "price": 1010.0,
             "costs": 0}]
    before = replay(recs, bars, "2026-01-16 18:00", SESSIONS, RiskConfig())
    assert before.weekly_breach(RiskConfig())[0] and before.warnings      # the bug, as it was

    acts = {"ABC": [corporate.Action("ABC", date(2026, 1, 14), 2.0)]}
    rp = replay(corporate.adjust_records(recs, acts), bars, "2026-01-16 18:00", SESSIONS, RiskConfig())
    assert rp.weekly_breach(RiskConfig()) == ("", "") and rp.warnings == []
    [t] = rp.trades
    assert (t.qty, t.entry, t.exit, t.pnl) == (300, 500.0, 505.0, 1500.0)    # same rupees, today's shares
    assert t.r_multiple == pytest.approx(1500 / (300 * 25))


def test_the_broker_and_the_ledger_follow_a_bonus_on_a_held_position(tmp_path):
    acts = write(tmp_path, BONUS)
    rows = [(100.0, 101, 99, 100)] * 5 + [(50.5, 51, 50, 50.5)] * 5     # real prices: halved from 12 Jan
    b = PaperBroker(tmp_path / "book.json", RiskConfig(), slippage_bps=0.0, actions=acts)
    b.submit(ticket(), "2026-01-02 18:00")
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "open", "at": "2026-01-02 18:00", "cash": 1_000_000})
    record_fills(ledger, b.sync("2026-01-09 18:00", {"ABC": ohlc(*rows[:5])}))
    # The bonus is noted on the 13th; the saved history is now adjusted.
    adjusted = ohlc(*([(50.0, 50.5, 49.5, 50.0)] * 5 + rows[5:]))
    events = b.sync("2026-01-16 18:00", {"ABC": adjusted})
    assert events == [] and "2-for-1" in b.notes[0]                  # no fake stop at the open
    [oco] = b.open_orders()
    assert (oco["qty"], oco["stop"], oco["target"], oco["entry_price"]) == (200, 47.5, 55.0, 50.0)

    rp = replay(corporate.adjust_records(ledger.records(), acts), {"ABC": adjusted}, "2026-01-16 18:00",
                SESSIONS, RiskConfig())
    assert rp.warnings == [] and rp.weekly_breach(RiskConfig()) == ("", "")
    assert rp.holdings["ABC"].qty == 200 and rp.holdings["ABC"].stop == 47.5
    assert rp.end_equity == pytest.approx(1_000_000 + 200 * 0.5 - 100 * 100 * 0.00125, abs=0.01)


def test_an_order_placed_after_the_action_was_noted_is_left_alone(tmp_path):
    acts = write(tmp_path, BONUS)
    b = PaperBroker(tmp_path / "book.json", RiskConfig(), slippage_bps=0.0, actions=acts)
    b.submit(held("2026-01-14", entry=50.0, stop=47.5, target=55.0, checked="2026-01-13 08:30"), "2026-01-13 08:30")
    b.sync("2026-01-13 18:00", {"ABC": ohlc(*[(50.0, 51, 49, 50)] * 7)})
    [oco] = b.open_orders()
    assert (oco["qty"], oco["stop"]) == (100, 47.5) and not oco.get("actions")
