import json

import pandas as pd
import pytest

from swingbot.broker import OrderRefused, PaperBroker, record_fills
from swingbot.broker.dhan import DhanBroker
from swingbot.paper import Ledger, replay
from swingbot.paper.cli import main as paper_main
from swingbot.risk import RiskConfig

# Mon 5 Jan .. Fri 16 Jan 2026: ten sessions.
SESSIONS = pd.bdate_range("2026-01-05", periods=10)


def ohlc(*rows, start="2026-01-05"):
    """Each row is (open, high, low, close), one per session from `start`."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=pd.bdate_range(start, periods=len(rows)))
    df["volume"] = 1000.0
    return df


def flat(n, price=100.0):
    return [(price, price + 1, price - 1, price)] * n


def ticket(ticker="ABC", qty=100, entry=100.0, stop=95.0, target=110.0, checked="2026-01-02 18:00",
           stop_type="STOP_LOSS_MARKET"):
    """A ticket as `swingbot-risk ticket` writes it, with the stop-loss market leg."""
    common = {"exchangeSegment": "NSE_EQ", "productType": "CNC", "tradingSymbol": ticker, "securityId": None}
    stop_leg = {**common, "transactionType": "SELL", "orderType": stop_type, "quantity": qty, "triggerPrice": stop}
    if stop_type == "STOP_LOSS":
        stop_leg["price"] = round(stop * 0.995, 2)
    return {
        "paper_only": True, "fingerprint": "f" * 16, "checked_at": checked,
        "entry": {**common, "transactionType": "BUY", "orderType": "LIMIT", "validity": "DAY",
                  "quantity": qty, "price": entry},
        "exits": {"orderFlag": "OCO", "orderType": "MARKET",
                  "target": {**common, "transactionType": "SELL", "orderType": "MARKET", "quantity": qty,
                             "triggerPrice": target},
                  "stop": stop_leg},
    }


def broker(tmp_path, **kw):
    return PaperBroker(tmp_path / "book.json", RiskConfig(), slippage_bps=kw.pop("slippage_bps", 0.0), **kw)


def run(tmp_path, rows, t=None, as_of="2026-01-16 18:00", **kw):
    b = broker(tmp_path, **kw)
    b.submit(t or ticket(), "2026-01-02 18:00")       # Friday evening: live for Monday 5 Jan
    return b, b.sync(as_of, {"ABC": ohlc(*rows)})


def sells(events):
    return [(e.at, e.price, e.reason) for e in events if getattr(e, "side", None) == "sell"]


def test_entry_fills_at_the_open_when_it_opens_under_the_limit(tmp_path):
    _, ev = run(tmp_path, [(99.0, 101, 98, 100)] + flat(9))
    [buy] = ev
    assert (buy.at, buy.price, buy.reason, buy.verdict) == ("2026-01-05 09:15", 99.0, "entry-gap", "PASS")
    assert (buy.stop, buy.target, buy.qty) == (95.0, 110.0, 100)


def test_entry_needs_the_low_to_trade_under_the_limit(tmp_path):
    b, ev = run(tmp_path, [(101.0, 103, 100.0, 102)] + flat(9))     # touched 100, never under
    assert ev == [] and b.open_orders() == []
    assert b.orders[0]["status"] == "expired" and "lapsed" in b.notes[0]

    b, ev = run(tmp_path / "2", [(101.0, 103, 99.5, 102)] + flat(9))
    assert [(e.at, e.price) for e in ev] == [("2026-01-05 15:30", 100.0)]


def test_a_day_order_is_live_for_one_session_only(tmp_path):
    rows = [(102, 104, 101, 103), (99, 100, 98, 99)] + flat(8)    # the dip comes a day late
    b, ev = run(tmp_path, rows)
    assert ev == [] and b.orders[0]["status"] == "expired"


def test_no_stop_on_the_entry_session(tmp_path):
    # Dhan takes no Forever Order on shares bought today: the entry day's low under the stop sells nothing.
    rows = [(100, 101, 90, 92), (93, 94, 92, 93)] + flat(8, 93)
    b, ev = run(tmp_path, rows)
    assert sells(ev) == [("2026-01-06 09:15", 93.0, "stop-gap")]   # the stop goes in for Tuesday, gapped through
    assert b.orders[1]["live_from"] == "2026-01-06"


def test_stop_and_target_intraday(tmp_path):
    _, ev = run(tmp_path, flat(1) + [(100, 101, 95.0, 96)] + flat(8))
    assert sells(ev) == [("2026-01-06 15:30", 95.0, "stop")]
    _, ev = run(tmp_path / "2", flat(1) + [(100, 110.0, 99, 109)] + flat(8))
    assert sells(ev) == [("2026-01-06 15:30", 110.0, "target")]


def test_both_sides_in_one_bar_takes_the_stop(tmp_path):
    _, ev = run(tmp_path, flat(1) + [(100, 112, 94, 105)] + flat(8))
    assert sells(ev) == [("2026-01-06 15:30", 95.0, "stop")]


def test_gaps_at_the_open(tmp_path):
    _, ev = run(tmp_path, flat(1) + [(90.0, 92, 88, 91)] + flat(8))
    assert sells(ev) == [("2026-01-06 09:15", 90.0, "stop-gap")]    # worse than the stop, but taken
    _, ev = run(tmp_path / "2", flat(1) + [(115.0, 116, 114, 115)] + flat(8))
    assert sells(ev) == [("2026-01-06 09:15", 115.0, "target-gap")]


def test_slippage_on_market_sells(tmp_path):
    _, ev = run(tmp_path, flat(1) + [(100, 101, 94, 96)] + flat(8), slippage_bps=10)
    assert sells(ev)[0][1] == pytest.approx(94.9)                  # 95 less 10 bps, down to the tick


def test_an_old_stop_limit_leg_can_miss_a_gap(tmp_path):
    t = ticket(stop_type="STOP_LOSS")                              # limit 94.53
    b, ev = run(tmp_path, flat(1) + [(90.0, 92, 88, 91)] + flat(8, 91), t=t)
    assert sells(ev) == []
    assert any("NO stop" in n for n in b.notes)


def test_trailing_moves_the_stop_up_for_the_next_session(tmp_path):
    rows = flat(15) + [(100, 106, 99, 105.5)] + [(106, 107, 104.5, 106)] * 3 + [(105, 105, 99.5, 100)]
    bars = ohlc(*rows, start="2025-12-15")
    b = broker(tmp_path, trail=True)
    b.submit(ticket(checked="2026-01-02 10:00"), "2026-01-02 10:00")   # live on Fri 2 Jan
    ev = b.sync("2026-01-12 18:00", {"ABC": bars})
    moves = [e for e in ev if not hasattr(e, "side")]
    # The +1R close of 105.5 lifts it past breakeven to 2 x ATR under the best close, then again at 106.
    assert [(m.at, m.stop) for m in moves] == [("2026-01-05 15:35", 100.75), ("2026-01-06 15:35", 101.25)]
    assert "R7 trail" in moves[0].reason
    assert sells(ev) == [("2026-01-09 15:30", 101.25, "stop")]         # the trailed stop, not 95


def test_close_sells_at_the_next_open_and_drops_the_oco(tmp_path):
    b = broker(tmp_path)
    b.submit(ticket(), "2026-01-02 18:00")
    rows = flat(2) + [(103, 120, 90, 104)] + flat(7)
    b.sync("2026-01-06 18:00", {"ABC": ohlc(*rows)})
    b.close("ABC", "2026-01-06 18:00")
    ev = b.sync("2026-01-16 18:00", {"ABC": ohlc(*rows)})
    assert sells(ev) == [("2026-01-07 09:15", 103.0, "close")]
    assert b.open_orders() == []


def test_stale_prices_hold_the_book_back(tmp_path):
    b = broker(tmp_path)
    b.submit(ticket(), "2026-01-02 18:00")
    assert b.sync("2026-01-06 18:00", {"ABC": ohlc(*flat(3), start="2025-12-31")}) == []
    assert b.open_orders()[0]["status"] == "open"           # not lapsed: Monday's bar is not in yet
    assert any("settled to 2026-01-02" in n for n in b.notes)


def test_refusals(tmp_path):
    b = broker(tmp_path)
    with pytest.raises(OrderRefused, match="paper_only"):
        b.submit({**ticket(), "paper_only": False}, "2026-01-02 18:00")
    with pytest.raises(OrderRefused, match="kill switch"):
        b.submit(ticket(), "2026-01-02 18:00", kill_switch_latched=True)
    with pytest.raises(OrderRefused, match="PASS is from"):
        b.submit(ticket(checked="2025-12-29 18:00"), "2026-01-02 18:00")
    b.submit(ticket(), "2026-01-02 18:00")
    with pytest.raises(OrderRefused, match="does not add"):
        b.submit(ticket(), "2026-01-02 18:05")
    b.sync("2026-01-07 18:00", {"ABC": ohlc(*flat(3))})
    with pytest.raises(OrderRefused, match="only moves up"):
        b.modify_stop("ABC", 90.0, "2026-01-07 18:00")
    with pytest.raises(OrderRefused, match="already settled"):
        b.submit(ticket("DEF", checked="2026-01-05 18:00"), "2026-01-05 18:00")
    with pytest.raises(NotImplementedError):
        DhanBroker()


def test_the_book_survives_a_restart(tmp_path):
    b = broker(tmp_path)
    oid = b.submit(ticket(), "2026-01-02 18:00")
    b.save()
    again = broker(tmp_path)
    assert again.open_orders()[0]["id"] == oid
    again.cancel(oid, "2026-01-03 10:00")
    assert again.open_orders() == []


def test_fills_replay_into_the_paper_account(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "open", "at": "2026-01-02 09:00", "cash": 1_000_000})
    rows = [(99.0, 101, 98, 100)] + flat(3) + [(100, 110.5, 99, 110)] + flat(5, 110)
    b, ev = run(tmp_path, rows)
    assert record_fills(ledger, ev) == 2
    rp = replay(ledger.records(), {"ABC": ohlc(*rows)}, "2026-01-16 18:00", SESSIONS)
    [t] = rp.trades
    assert (t.entry, t.exit) == (99.0, 110.0)
    assert rp.breaks == [] and rp.holdings == {}


def test_cli_submit_settle_report(tmp_path, monkeypatch):
    prices = tmp_path / "prices"
    prices.mkdir()
    rows = [(99.0, 101, 98, 100)] + [(100, 101, 94, 95)] + flat(8, 95)
    ohlc(*rows).rename_axis("date").to_csv(prices / "ABC.csv")
    ohlc(*flat(10, 1000)).rename_axis("date").to_csv(prices / "NIFTY50.csv")
    cfg = tmp_path / "data.toml"
    cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    (tmp_path / "ticket.json").write_text(json.dumps(ticket()))
    common = ["--ledger", str(tmp_path / "l.jsonl"), "--book", str(tmp_path / "book.json"),
              "--state-dir", str(tmp_path / "state"), "--data-config", str(cfg)]
    assert paper_main(common + ["open", "--cash", "1000000", "--at", "2026-01-02"]) == 0
    assert paper_main(common + ["submit", str(tmp_path / "ticket.json"), "--at", "2026-01-02 18:00"]) == 0
    assert paper_main(common + ["settle", "--as-of", "2026-01-16 18:00"]) == 0
    lines = [json.loads(x) for x in (tmp_path / "l.jsonl").read_text().splitlines()]
    assert [(r["kind"], r.get("side"), r.get("reason")) for r in lines] == [
        ("open", None, None), ("fill", "buy", "entry-gap"), ("fill", "sell", "stop")]
    assert paper_main(common + ["report", "--as-of", "2026-01-16 18:00"]) == 0


def test_takes_a_real_ticket(tmp_path):
    from datetime import date

    from swingbot.risk import Order, make_ticket

    order = Order("ABC", "buy", 100, 100.0, 95.0, 110.0, "Tech")
    t = make_ticket(order, {"fingerprint": order.fingerprint, "status": "PASS", "at": "2026-01-02 18:00"},
                    date(2026, 1, 2), RiskConfig())
    b = broker(tmp_path)
    b.submit(t, "2026-01-02 18:00")
    o = b.open_orders()[0]
    assert (o["limit"], o["stop"], o["target"], o["sector"]) == (100.0, 95.0, 110.0, "")
