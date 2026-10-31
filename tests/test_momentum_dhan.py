import json

import pytest
from test_dhan import Clock, FakeDhan

from swingbot.broker import OrderRefused
from swingbot.broker.dhan import DhanAPI, DhanConfig, RateLimit
from swingbot.broker.dhan_cli import main as dhan_main
from swingbot.broker.momentum_dhan import MomentumConfig, MomentumDhan, check, plan
from swingbot.risk import NSECalendar

SYMS = [f"S{i:02d}" for i in range(24)]
SCRIPS = {s: str(5000 + i) for i, s in enumerate(SYMS)}


class FeedDhan(FakeDhan):
    """FakeDhan plus the market feed, at one price per security id."""

    def __init__(self, prices):
        super().__init__()
        self.prices = prices

    def __call__(self, method, url, headers, body):
        if url.endswith("/marketfeed/ltp"):
            payload = json.loads(body)
            self.calls.append((method, "/marketfeed/ltp", payload))
            feed = {str(i): {"last_price": self.prices[str(i)]} for i in payload["NSE_EQ"]}
            return 200, json.dumps({"data": {"NSE_EQ": feed}, "status": "success"}).encode()
        return super().__call__(method, url, headers, body)


def ledger(*months):
    return {"start": "2026-10-30", "capital": 1e6, "months": [{"date": d, "picks": p} for d, p in months]}


def make(tmp_path, prices=None, live=False, monkeypatch=None):
    prices = prices or {sid: 100.0 + i for i, sid in enumerate(SCRIPS.values())}
    fake, clock = FeedDhan(prices), Clock()
    if live:
        monkeypatch.setenv("SWINGBOT_DHAN_LIVE", "yes")
    config = DhanConfig(mode="live" if live else "sandbox", state_dir=tmp_path)
    api = DhanAPI(config, "cid", "tok", transport=fake, limit=RateLimit(None, clock, clock.sleep))
    bot = MomentumDhan(api, SCRIPS, MomentumConfig(capital=1_000_000), NSECalendar.load(), clock, clock.sleep)
    return bot, fake


NOV = ("2026-11-30", SYMS[:20])
DEC = ("2026-12-31", SYMS[2:22])   # S00, S01 leave; S20, S21 join


def test_plan_sells_first_in_equal_slots_and_needs_every_price():
    prices = {s: 100.0 for s in SYMS}
    orders, slot = plan(SYMS[2:22], {"S00": 50, "S02": 5000}, 100_000.0, prices)
    assert slot == pytest.approx((100_000 + 5050 * 100) * 0.998 / 20)
    assert [o["side"] for o in orders[:2]] == ["SELL", "SELL"]
    assert orders[0] == {"symbol": "S00", "side": "SELL", "qty": 50, "price": 100.0}   # left the picks
    assert orders[1]["symbol"] == "S02" and orders[1]["qty"] == 5000 - int(slot // 100)   # trimmed to its slot
    assert all(o["side"] == "BUY" for o in orders[2:]) and len(orders) == 21
    with pytest.raises(OrderRefused, match="no price for S21"):
        plan(SYMS[2:22], {}, 1e6, {s: p for s, p in prices.items() if s != "S21"})


def test_check_refuses_a_wrong_pick_count_and_an_oversized_buy():
    with pytest.raises(OrderRefused, match="19 distinct picks"):
        check(SYMS[:19], [], 1000.0, {}, None)
    with pytest.raises(OrderRefused, match="past one slot"):
        check(SYMS[:20], [{"symbol": "S00", "side": "BUY", "qty": 30, "price": 100.0}], 1000.0, {}, None)
    with pytest.raises(OrderRefused, match="Dhan's holdings show 3"):
        check(SYMS[:20], [{"symbol": "S00", "side": "SELL", "qty": 5, "price": 100.0}], 1000.0, {"S00": 5},
              {"S00": 3})


def test_first_month_buys_twenty_then_next_month_sells_before_it_buys(tmp_path):
    bot, fake = make(tmp_path)
    out = bot.rebalance(ledger(NOV), "2026-12-01 10:00")             # dry run
    assert len(out["planned"]) == 20 and out["sent"] == []
    assert not any(p == "/orders" for _, p, _ in fake.calls)
    feed = next(b for _, p, b in fake.calls if p == "/marketfeed/ltp")
    assert "dhanClientId" not in feed                                 # the feed takes only exchange lists

    out = bot.rebalance(ledger(NOV), "2026-12-01 10:00", send=True)
    sent = [b for m, p, b in fake.calls if (m, p) == ("POST", "/orders")]
    assert len(sent) == 20 and all(b["transactionType"] == "BUY" and b["productType"] == "CNC"
                                   and b["orderType"] == "LIMIT" and not b["afterMarketOrder"] for b in sent)
    assert sent[0]["price"] == pytest.approx(100.5)                   # 0.5% through the last price
    bot.sync()
    assert len(bot.book["holdings"]) == 20 and bot.book["cash"] > 0
    assert bot.book["last_month"] == "2026-11-30"

    fake.holdings = {SCRIPS[s]: q for s, q in bot.book["holdings"].items()}
    before = len(fake.calls)
    out = bot.rebalance(ledger(NOV, DEC), "2027-01-01 08:30", send=True)   # a holiday-free morning: AMO
    orders = [b for m, p, b in fake.calls[before:] if (m, p) == ("POST", "/orders")]
    sides = [b["transactionType"] for b in orders]
    assert sides[:2] == ["SELL", "SELL"] and "BUY" in sides
    assert sides.index("BUY") > max(i for i, s in enumerate(sides) if s == "SELL")
    assert all(b["afterMarketOrder"] for b in orders)
    bot.sync()
    assert "S00" not in bot.book["holdings"] and "S21" in bot.book["holdings"]
    assert bot.book["cash"] >= 0


def test_refusals_halt_stale_open_orders_and_live(tmp_path, monkeypatch):
    bot, fake = make(tmp_path)
    with pytest.raises(OrderRefused, match="sessions ago"):
        bot.rebalance(ledger(NOV), "2026-12-10 10:00")
    bot.halt("testing")
    with pytest.raises(OrderRefused, match="halted: .* testing"):
        bot.rebalance(ledger(NOV), "2026-12-01 10:00")
    bot.halt_path.unlink()

    fake.order_status = {f"O{i}": "PENDING" for i in range(1, 30)}
    bot.rebalance(ledger(NOV), "2026-12-01 10:00", send=True)
    with pytest.raises(OrderRefused, match="still open"):
        bot.rebalance(ledger(NOV), "2026-12-01 11:00", send=True)

    live, lfake = make(tmp_path / "live", live=True, monkeypatch=monkeypatch)
    out = live.rebalance(ledger(NOV), "2026-12-01 10:00")             # the shadow dry run works
    assert len(out["planned"]) == 20
    with pytest.raises(OrderRefused, match="read-only"):
        live.rebalance(ledger(NOV), "2026-12-01 10:00", send=True)
    assert not any(p == "/orders" for _, p, _ in lfake.calls)


def test_buys_wait_when_the_sells_have_not_finished(tmp_path):
    bot, fake = make(tmp_path)
    bot.rebalance(ledger(NOV), "2026-12-01 10:00", send=True)
    bot.sync()
    n = len(bot.book["orders"])
    fake.order_status = {f"O{i}": "PENDING" for i in range(21, 60)}
    fake.holdings = {SCRIPS[s]: q for s, q in bot.book["holdings"].items()}
    bot.rebalance(ledger(NOV, DEC), "2027-01-01 10:00", send=True)
    new = bot.book["orders"][n:]
    assert {o["side"] for o in new} == {"SELL"} and any("buys wait" in x for x in bot.notes)


def test_cli_halt_and_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "broker.toml").write_text('[dhan]\nstate_dir = "state"\nscrip_master = "scrips.csv"\n'
                                          '[momentum]\nledger = "trial.json"\n')
    (tmp_path / "scrips.csv").write_text("symbol,security_id\n" + "".join(f"{s},{i}\n" for s, i in SCRIPS.items()))
    (tmp_path / "trial.json").write_text(json.dumps(ledger(NOV)))
    (tmp_path / "prices.json").write_text(json.dumps({s: 200 for s in SYMS}))
    assert dhan_main(["--config", "broker.toml", "rebalance", "--halt", "away"]) == 0
    assert (tmp_path / "state" / "momentum_halt").exists()
    monkeypatch.setenv("SWINGBOT_DHAN_CLIENT_ID", "cid")
    monkeypatch.setenv("SWINGBOT_DHAN_TOKEN", "tok")
    assert dhan_main(["--config", "broker.toml", "--as-of", "2026-12-01 10:00", "rebalance",
                      "--prices", "prices.json"]) == 1
    assert "halted" in capsys.readouterr().err
    (tmp_path / "state" / "momentum_halt").unlink()
    assert dhan_main(["--config", "broker.toml", "--as-of", "2026-12-01 10:00", "rebalance",
                      "--prices", "prices.json"]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing sent" in out and out.count("BUY") == 20
    assert dhan_main(["--config", "broker.toml", "rebalance", "--send", "--prices", "prices.json"]) == 2
