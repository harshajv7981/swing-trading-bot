import json

import pandas as pd
import pytest

from swingbot.paper import Ledger, replay
from swingbot.paper.ledger import stop_fill
from swingbot.paper.cli import main as paper_main
from swingbot.risk import KillSwitch, Order, RiskConfig, check_order

from test_backtest import bars

# Mon 5 Jan .. Fri 16 Jan 2026: ten sessions.
SESSIONS = pd.bdate_range("2026-01-05", periods=10)
AS_OF = "2026-01-16 18:00"


def stock(closes):
    return bars(closes, start="2026-01-05")


def opened(cash=1_000_000, at="2026-01-05 09:00"):
    return [{"kind": "open", "at": at, "cash": cash}]


def buy(ticker="ABC", qty=100, price=100.0, at="2026-01-05 09:20", stop=95.0, verdict="PASS", costs=0.0, **kw):
    return {"kind": "fill", "at": at, "ticker": ticker, "side": "buy", "qty": qty, "price": price,
            "stop": stop, "target": price * 1.1, "sector": kw.pop("sector", "Tech"), "verdict": verdict,
            "costs": costs, **kw}


def sell(ticker="ABC", qty=100, price=110.0, at="2026-01-12 10:00", costs=0.0):
    return {"kind": "fill", "at": at, "ticker": ticker, "side": "sell", "qty": qty, "price": price, "costs": costs}


def rules(rp):
    return [f.rule for f in rp.breaks]


def test_equity_curve_and_a_winning_trade():
    rp = replay(opened() + [buy(), sell()], {"ABC": stock(list(range(100, 110)))}, AS_OF, SESSIONS)
    assert len(rp.equity) == 10
    assert rp.equity.iloc[0] == pytest.approx(1_000_000)          # bought at 100, closed at 100
    assert rp.equity.iloc[2] == pytest.approx(1_000_200)          # third close 102
    assert rp.end_equity == pytest.approx(1_001_000)              # sold at 110
    [t] = rp.trades
    assert t.pnl == pytest.approx(1_000) and t.r_multiple == pytest.approx(2.0)  # 10 gained / 5 risked
    assert rp.breaks == [] and rp.holdings == {}


def test_costs_come_off_the_trade_and_the_cash():
    rp = replay(opened() + [buy(costs=12.5), sell(costs=13.75)], {"ABC": stock([100] * 10)}, AS_OF, SESSIONS)
    assert rp.trades[0].pnl == pytest.approx(1_000 - 26.25)
    assert rp.cash == pytest.approx(1_000_000 + 1_000 - 26.25)
    assert rp.costs == pytest.approx(26.25)


def test_max_drawdown_from_the_peak():
    closes = [100, 110, 120, 90, 95, 100, 100, 100, 100, 100]
    rp = replay(opened(100_000) + [buy(qty=100, stop=80.0)], {"ABC": stock(closes)}, AS_OF, SESSIONS)
    dd, top, trough = rp.max_drawdown()
    # Peak 102,000 at the 120 close, trough 99,000 at 90: 2.94%.
    assert dd == pytest.approx(3_000 / 102_000 * 100)
    assert (top, trough) == ("2026-01-07", "2026-01-08")


def test_return_against_the_benchmark():
    bench = stock([1000 + 10 * i for i in range(10)])["close"]
    rp = replay(opened() + [buy(), sell()], {"ABC": stock(list(range(100, 110)))}, AS_OF, SESSIONS)
    s = rp.summary(bench)
    assert s["return_pct"] == pytest.approx(0.1)
    assert s["benchmark_return_pct"] == pytest.approx(9.0)        # 1000 -> 1090, no prior close
    assert s["excess_return_pct"] == pytest.approx(-8.9)


def test_rule_breaks_are_listed():
    recs = opened(100_000) + [
        buy("ABC", qty=100, stop=None, verdict="none"),             # no stop, no PASS
        buy("BIG", qty=300, price=100.0, stop=90.0),                # 30k = 30% of equity, risk 3% of equity
        sell("XYZ", qty=5),                                          # a short
    ]
    rp = replay(recs, {"ABC": stock([100] * 10), "BIG": stock([100] * 10)}, AS_OF, SESSIONS)
    assert set(rules(rp)) == {"risk gate", "R1 stop and target", "R2 risk per trade", "R3 position cap",
                              "long only"}


def test_stop_may_only_move_up_and_breaches_are_flagged():
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    b = stock(closes)
    b.loc[SESSIONS[6], "low"] = 90.0                                  # a session trades through the stop
    recs = opened() + [buy(),
                       {"kind": "stop", "at": "2026-01-07 16:00", "ticker": "ABC", "stop": 100.0},
                       {"kind": "stop", "at": "2026-01-08 16:00", "ticker": "ABC", "stop": 97.0}]
    rp = replay(recs, {"ABC": b}, AS_OF, SESSIONS)
    assert rules(rp) == ["R7 stop only moves up"]
    assert any(w.rule == "stop" and "through its stop" in w.detail for w in rp.warnings)


@pytest.mark.parametrize("open_, low, want", [
    (100.0, 96.0, None),    # never reaches the stop
    (100.0, 94.0, 95.0),    # trades through it in the session: fills at the stop
    (95.0, 93.0, 95.0),     # opens exactly at the stop
    (90.0, 88.0, 90.0),     # gaps down through it: fills at the open, not the stop
])
def test_stop_fill_on_a_daily_bar(open_, low, want):
    assert stop_fill(95.0, open_, low) == want


def test_a_gap_through_the_stop_warns_with_the_open_as_the_fill():
    b = stock([100] * 10)
    b.loc[SESSIONS[3], ["open", "low"]] = [90.0, 88.0]               # gaps down through the 95 stop
    rp = replay(opened() + [buy()], {"ABC": b}, AS_OF, SESSIONS)
    [w] = [w for w in rp.warnings if w.rule == "stop"]
    assert "opened at 90.00, below its stop 95.00" in w.detail and "about 90.00" in w.detail


def test_entry_after_the_weekly_kill_level_is_a_break():
    # Week 1 ends at 1,00,000; by Wednesday of week 2 the account is 6% down, then it buys again.
    closes = [100] * 5 + [100, 70, 70, 70, 70]
    recs = opened(100_000) + [buy("ABC", qty=200, stop=60.0, verdict="PASS"),
                              buy("DEF", qty=10, price=50.0, stop=45.0, at="2026-01-14 09:20")]
    rp = replay(recs, {"ABC": stock(closes), "DEF": stock([50] * 10)}, AS_OF, SESSIONS,
                RiskConfig(max_position_pct=100, risk_pct=2.0, hard_max_risk_pct=2.0))
    assert "R5 weekly kill switch" in rules(rp)


def test_portfolio_feeds_the_risk_engine():
    closes = [100] * 5 + [104, 104, 104, 104, 106]
    rp = replay(opened() + [buy()], {"ABC": stock(closes)}, AS_OF, SESSIONS)
    p = rp.portfolio()
    assert p.equity == pytest.approx(1_000_600) and p.cash == pytest.approx(990_000)
    assert p.week_open_equity == pytest.approx(1_000_000)   # last close of the week before
    assert p.day_open_equity == pytest.approx(1_000_400)    # Thursday's close, not today's
    [pos] = p.positions
    assert (pos.ticker, pos.qty, pos.stop, pos.sector) == ("ABC", 100, 95.0, "Tech")


def test_r6_fires_on_the_paper_portfolio():
    # Thursday closes at 100, Friday at 96: 100 shares on 1,00,000 is a 0.4% day,
    # 1,000 shares on 1,00,000 is a 4% day, past R6's 2%.
    closes = [100] * 9 + [96]
    rp = replay(opened(100_000) + [buy(qty=1_000, stop=90.0)], {"ABC": stock(closes)}, AS_OF, SESSIONS)
    p = rp.portfolio()
    assert p.day_open_equity == pytest.approx(100_000) and p.equity == pytest.approx(96_000)
    v = check_order(Order("DEF", "buy", 1, 100.0, 95.0, 110.0, "Energy"), p, RiskConfig())
    assert "R6 daily loss" in {c.rule for c in v.checks if not c.ok}


def test_portfolio_mid_session_uses_the_last_close_as_the_day_open():
    # Before Friday's close only Thursday's is known: the day open is Thursday's close.
    closes = [100] * 5 + [104, 104, 104, 104, 106]
    rp = replay(opened() + [buy()], {"ABC": stock(closes[:9])}, "2026-01-16 11:00", SESSIONS)
    p = rp.portfolio()
    assert p.day_open_equity == pytest.approx(1_000_400) and p.equity == pytest.approx(1_000_400)
    assert p.week_open_equity == pytest.approx(1_000_000)


def test_week_open_on_a_monday_morning_is_last_fridays_close():
    closes = [100] * 4 + [103, 103, 103, 103, 103, 103]
    rp = replay(opened() + [buy()], {"ABC": stock(closes[:5])}, "2026-01-12 10:00", SESSIONS)
    assert rp.portfolio().week_open_equity == pytest.approx(1_000_300)


def test_weekly_breach_is_found_from_the_closes():
    closes = [100] * 5 + [100, 70, 70, 100, 100]     # 6% down Tuesday and Wednesday of week 2, then back
    rp = replay(opened(100_000) + [buy(qty=200, stop=60.0)], {"ABC": stock(closes)}, AS_OF, SESSIONS,
                RiskConfig(max_position_pct=100, risk_pct=2.0, hard_max_risk_pct=2.0))
    week, reason = rp.weekly_breach(RiskConfig())
    assert week == "2026-W03" and "2026-01-14" in reason   # the latest breach
    assert replay(opened() + [buy()], {"ABC": stock([100] * 10)}, AS_OF, SESSIONS).weekly_breach(RiskConfig()) \
        == ("", "")


def test_paper_report_latches_the_kill_switch(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    stock([100] * 5 + [100, 70, 70, 100, 100]).to_csv(prices / "ABC.csv", index_label="date")
    data_cfg = tmp_path / "data.toml"
    data_cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    ledger, state = tmp_path / "ledger.jsonl", tmp_path / "state"
    ledger.write_text("\n".join(json.dumps(r) for r in opened(100_000) + [buy(qty=200, stop=60.0)]) + "\n")
    paper_main(["--ledger", str(ledger), "--data-config", str(data_cfg), "--state-dir", str(state),
                "portfolio", "--as-of", AS_OF, "--out", str(tmp_path / "p.json")])
    assert KillSwitch(state).latched and KillSwitch(state).state()["week"] == "2026-W03"


def test_never_reads_after_the_as_of_time():
    recs = opened() + [buy(), sell(at="2026-01-14 10:00")]
    rp = replay(recs, {"ABC": stock(list(range(100, 110)))}, "2026-01-12 18:00", SESSIONS)
    assert len(rp.equity) == 6 and rp.trades == []           # the sell is after the as-of time
    assert "ABC" in rp.holdings


def test_gate_progress():
    s = replay(opened(at="2026-01-05 09:00") + [buy(), sell()], {"ABC": stock([100] * 10)},
               AS_OF, SESSIONS).summary()
    assert s["gate"] == {"months": 0, "months_needed": 6, "trades": 1, "trades_needed": 50, "met": False}


def test_ledger_needs_one_opening_balance(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(ValueError, match="no opening balance"):
        ledger.append(buy())
    ledger.append(opened()[0])
    with pytest.raises(ValueError, match="already has"):
        ledger.append(opened()[0])


# --- the CLI end to end on saved CSVs ------------------------------------------

def test_cli_round_trip(tmp_path, capsys):
    prices = tmp_path / "prices"
    prices.mkdir()
    stock(list(range(100, 110))).rename_axis("date").to_csv(prices / "ABC.csv")
    stock([1000 + 10 * i for i in range(10)]).rename_axis("date").to_csv(prices / "NIFTY50.csv")
    cfg = tmp_path / "data.toml"
    cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    state = tmp_path / "state"
    state.mkdir()
    order = Order("ABC", "buy", 100, 100.0, 95.0, 110.0, "Tech")
    (tmp_path / "order.json").write_text(json.dumps(order.to_json()))
    (state / "risk_verdicts.jsonl").write_text(json.dumps(
        {"at": "2026-01-05 09:10", "status": "PASS", "fingerprint": order.fingerprint}) + "\n")
    base = ["--ledger", str(tmp_path / "l.jsonl"), "--data-config", str(cfg), "--state-dir", str(state)]

    assert paper_main(base + ["open", "--cash", "1000000", "--at", "2026-01-05"]) == 0
    assert paper_main(base + ["fill", "buy", "--order", str(tmp_path / "order.json"), "--qty", "100",
                              "--price", "100", "--at", "2026-01-05 09:20", "--costs", "0"]) == 0
    assert json.loads((tmp_path / "l.jsonl").read_text().splitlines()[1])["verdict"] == "PASS"
    assert paper_main(base + ["fill", "sell", "ABC", "--qty", "100", "--price", "108",
                              "--at", "2026-01-14 10:00", "--costs", "0"]) == 0
    capsys.readouterr()
    assert paper_main(base + ["report", "--as-of", "2026-01-16", "--out", str(tmp_path / "rep")]) == 0
    out = capsys.readouterr().out
    assert "Closed trades 1" in out and "+1.60R" in out and "0 rule breaks" in out
    assert (tmp_path / "rep" / "equity.csv").exists()
    assert paper_main(base + ["portfolio", "--as-of", "2026-01-16", "--out", str(tmp_path / "p.json")]) == 0
    assert json.loads((tmp_path / "p.json").read_text())["cash"] == pytest.approx(1_000_800)


def test_cli_buy_without_pass_is_a_break(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    stock([100] * 10).rename_axis("date").to_csv(prices / "ABC.csv")
    cfg = tmp_path / "data.toml"
    cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    base = ["--ledger", str(tmp_path / "l.jsonl"), "--data-config", str(cfg), "--state-dir", str(tmp_path)]
    paper_main(base + ["open", "--cash", "100000", "--at", "2026-01-05"])
    paper_main(base + ["fill", "buy", "ABC", "--qty", "10", "--price", "100", "--stop", "95",
                       "--at", "2026-01-05 09:20"])
    assert paper_main(base + ["report", "--as-of", "2026-01-16"]) == 1
