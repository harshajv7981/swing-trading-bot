import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from swingbot.risk import (
    KillSwitch, NSECalendar, Order, PlanRefused, Portfolio, Position, ResetRefused, RiskConfig, SizingRefused,
    TicketRefused,
    check_order, make_ticket, plan_exit, size_position, trail_stop,
)
from swingbot.risk.cli import main as risk_main
from swingbot.risk.exits import ExitPlan, nse_tick
from swingbot.risk.sizing import gap_risk_per_share

from test_backtest import bars

CFG = RiskConfig()


def trend(n=260, start="2025-09-01", step=0.002, volume=2e6, seed=3):
    """A gently rising stock with some noise: above its 200-day SMA, liquid."""
    rng = np.random.default_rng(seed)
    b = bars(list(100 * np.cumprod(1 + step + rng.normal(0, 0.008, n))), start=start)
    b["volume"] = volume
    return b


def book(equity=500_000, cash=None, week=None, day=None, positions=()):
    return Portfolio(equity, equity if cash is None else cash, week or equity, day or equity, tuple(positions))


def entry(ticker="RELIANCE", qty=30, price=1000.0, stop=960.0, target=1080.0, sector="Energy", side="buy"):
    return Order(ticker, side, qty, price, stop, target, sector)


def check(order, portfolio=None, **kw):
    kw.setdefault("bars", trend())
    kw.setdefault("today", date(2026, 10, 26))
    kw.setdefault("results_date", date(2026, 11, 20))
    kw.setdefault("calendar", NSECalendar.load("config/nse_holidays.toml"))
    return check_order(order, portfolio or book(), CFG, **kw)


def failed(verdict):
    return {c.rule for c in verdict.checks if not c.ok}


# --- config and calendar -------------------------------------------------------

def test_repo_config_loads_and_matches_the_build_plan():
    cfg = RiskConfig.load("config/risk.toml")
    assert (cfg.risk_pct, cfg.hard_max_risk_pct, cfg.max_position_pct) == (1.0, 2.0, 15.0)
    assert (cfg.max_open_positions, cfg.max_per_sector, cfg.max_open_risk_pct) == (5, 2, 5.0)
    assert (cfg.weekly_kill_pct, cfg.daily_loss_pct, cfg.trend_sma) == (5.0, 2.0, 200)


@pytest.mark.parametrize("kw", [{"risk_pct": 2.5}, {"hard_max_risk_pct": 3.0, "risk_pct": 3.0}])
def test_config_refuses_risk_over_the_hard_cap(kw):
    with pytest.raises(ValueError):
        RiskConfig(**kw)


def test_config_refuses_unknown_settings(tmp_path):
    p = tmp_path / "r.toml"
    p.write_text("[per_trade]\nrisk_pc = 1.0\n")
    with pytest.raises(ValueError, match="risk_pc"):
        RiskConfig.load(p)


def test_calendar_skips_weekends_and_holidays():
    cal = NSECalendar.load("config/nse_holidays.toml")
    assert not cal.is_session(date(2026, 11, 10))  # Diwali Balipratipada, a Tuesday
    # Mon 9 Nov -> Thu 12 Nov: Tue is a holiday, so Wed and Thu count.
    assert cal.sessions_between(date(2026, 11, 9), date(2026, 11, 12)) == 2
    assert cal.sessions_between(date(2026, 11, 13), date(2026, 11, 16)) == 1  # Fri -> Mon
    assert cal.unknown_years(date(2026, 12, 30), date(2027, 1, 5)) == [2027]


# --- exit plans -----------------------------------------------------------------

def test_atr_stop_and_2r_target_round_down_to_the_tick():
    b = trend()
    plan = plan_exit("reliance", b, entry=1000.03, stop_rule="atr:2", target_rule="2R")
    assert plan.ticker == "RELIANCE" and plan.as_of == f"{b.index[-1]:%Y-%m-%d}"
    assert plan.stop < plan.entry < plan.target
    for price in (plan.stop, plan.target):
        assert round(price / 0.05, 6) == round(price / 0.05)
    assert plan.reward_risk == pytest.approx(2.0, abs=0.01)


@pytest.mark.parametrize("prices, tick", [
    ((249.99,), 0.01), ((250.0,), 0.05), ((1000.0,), 0.05), ((1000.05,), 0.10), ((5000.0,), 0.10),
    ((7500.0,), 0.50), ((10000.0,), 0.50), ((15000.0,), 1.00), ((20000.01,), 5.00),
    ((1010.0, 990.0), 0.10),  # a stop under the 1,000 edge keeps the entry's coarser tick
])
def test_nse_tick_follows_the_price_bands(prices, tick):
    assert nse_tick(*prices) == tick


def test_nse_tick_never_goes_below_the_configured_floor():
    assert nse_tick(100.0, floor=0.05) == 0.05
    assert nse_tick(3000.0, floor=0.05) == 0.10


def test_entry_rounds_up_and_exits_round_down_on_the_banded_tick():
    b = trend()
    px = ["open", "high", "low", "close"]
    b[px] = b[px] * 6000 / b["close"].iloc[-1]  # prices around 6,000, where the tick is 0.50
    plan = plan_exit("X", b, entry=6000.2, stop_rule="atr:2", target_rule="2R")
    assert plan.entry == 6000.5
    for price in (plan.stop, plan.target):
        assert round(price / 0.5, 6) == round(price / 0.5)


def test_stop_across_a_band_edge_uses_the_entry_tick():
    plan = plan_exit("X", trend(), entry=1010.0, stop_rule="price:990.07", target_rule="2R")
    assert plan.stop == 990.0  # 990.05 would be off a 0.10 grid


def test_low_stop_sits_a_tick_under_the_recent_low():
    b = trend()
    plan = plan_exit("X", b, entry=float(b["close"].iloc[-1]), stop_rule="low:10", target_rule="3R")
    assert plan.stop == pytest.approx(np.floor((b["low"].iloc[-10:].min() - 0.05) / 0.05) * 0.05)


@pytest.mark.parametrize("stop_rule, target_rule, words", [
    ("price:1001", "2R", "at or above the entry"),
    ("price:999.9", "2R", "noise"),
    ("atr:2", "price:990", "at or below the entry"),
    ("ma:50", "2R", "unknown stop rule"),
    ("atr:2", "moon", "unknown target rule"),
])
def test_bad_plans_are_refused_in_one_sentence(stop_rule, target_rule, words):
    with pytest.raises(PlanRefused, match=words):
        plan_exit("X", trend(), entry=1000.0, stop_rule=stop_rule, target_rule=target_rule)


def test_plan_needs_enough_history_for_an_atr():
    with pytest.raises(PlanRefused, match="ATR"):
        plan_exit("X", trend(n=10), entry=100.0)


def test_trailing_stop_moves_to_breakeven_then_trails_and_never_drops():
    closes = [100] * 20 + [104, 108, 111, 115, 120, 112, 105]
    b = bars(closes, start="2026-01-01")
    entry_date = f"{b.index[19]:%Y-%m-%d}"

    before = b.iloc[:22]  # highest close 108 < 1R target 110
    assert trail_stop(before, entry_date, 100.0, 90.0, 90.0)[0] == 90.0

    stops, stop = [], 90.0
    for i in range(23, len(b) + 1):
        stop, _ = trail_stop(b.iloc[:i], entry_date, 100.0, 90.0, stop)
        stops.append(stop)
    assert all(s >= 100.0 for s in stops)           # never below breakeven after +1R
    assert stops == sorted(stops)                   # never lowered, even as the price falls back
    assert stops[-1] > 100.0                        # and it trailed above breakeven at the 120 high


# --- sizing ---------------------------------------------------------------------

def test_r2_property_loss_at_the_stop_never_exceeds_the_limit():
    rng = np.random.default_rng(7)
    for _ in range(2000):
        equity = float(rng.uniform(50_000, 5_000_000))
        entry_px = float(rng.uniform(20, 5000))
        stop = round(entry_px * float(rng.uniform(0.80, 0.995)), 2)
        plan = ExitPlan("X", "2026-10-26", round(entry_px, 2), stop, "pct", round(entry_px * 1.2, 2), "price", 1.0)
        try:
            s = size_position(plan, book(equity), CFG, "IT")
        except SizingRefused:
            continue
        assert s.order.qty * (plan.entry - plan.stop) <= equity * 0.01 + 1e-6
        assert s.order.qty * gap_risk_per_share(plan.entry, plan.stop, CFG) <= equity * 0.01 + 1e-6
        assert s.order.qty * plan.entry <= equity * 0.15 + 1e-6


def test_tight_stop_is_capped_by_the_15_percent_rule():
    plan = ExitPlan("X", "2026-10-26", 1000.0, 995.0, "price", 1020.0, "price", 5.0)
    s = size_position(plan, book(500_000), CFG, "IT")
    assert (s.binding, s.order.qty) == ("position cap", 75)


def test_cash_and_open_risk_room_limit_the_size():
    plan = ExitPlan("X", "2026-10-26", 1000.0, 960.0, "price", 1080.0, "price", 20.0)
    assert size_position(plan, book(500_000, cash=20_000), CFG, "IT").binding == "cash"
    full = [Position(f"S{i}", 100, 1000.0, 940.0, "Banks") for i in range(4)]  # 24,000 open risk
    s = size_position(plan, book(500_000, positions=full), CFG, "IT")
    assert s.binding == "open risk room" and s.order.qty * 40 <= 1_000


def test_a_stop_too_far_for_one_share_is_refused_not_shrunk():
    plan = ExitPlan("MRF", "2026-10-26", 140_000.0, 120_000.0, "price", 180_000.0, "price", 3000.0)
    with pytest.raises(SizingRefused, match="too far"):
        size_position(plan, book(500_000), CFG, "Autos")


# --- the gate ---------------------------------------------------------------------

def test_a_sound_order_passes():
    v = check(entry())
    assert v.status == "PASS", v.text()


def test_r3_an_order_for_16_percent_of_equity_is_blocked():
    assert "R3 position cap" in failed(check(entry(qty=80, price=1000.0, stop=995.0, target=1010.0)))


def test_r4_a_sixth_position_or_a_third_it_stock_is_blocked():
    five = [Position(f"S{i}", 1, 100.0, 99.0, f"Sector{i}") for i in range(5)]
    assert "R4 open positions" in failed(check(entry(), book(positions=five)))
    two_it = [Position("TCS", 1, 100.0, 99.0, "IT"), Position("INFY", 1, 100.0, 99.0, "IT")]
    assert failed(check(entry("WIPRO", sector="IT"), book(positions=two_it))) == {"R4 sector"}


def test_r4_total_open_risk_is_capped():
    heavy = [Position("TCS", 400, 1000.0, 940.0, "IT")]  # 24,000 at risk
    assert "R4 open risk" in failed(check(entry(), book(positions=heavy)))


def test_r5_a_5_1_percent_week_blocks_and_trips_the_kill_switch():
    v = check(entry(), book(474_500, week=500_000, day=476_000))
    assert "R5 weekly kill switch" in failed(v) and v.trip_kill_switch
    latched = check(entry(), book(), kill_switch_latched=True)
    assert "R5 weekly kill switch" in failed(latched) and not latched.trip_kill_switch


def test_r6_a_2_1_percent_day_pauses_entries():
    assert failed(check(entry(), book(489_500, day=500_000))) == {"R6 daily loss"}


def test_r8_trend_and_liquidity_filters():
    falling = trend(step=-0.003)
    assert "R8 trend filter" in failed(check(entry(), bars=falling))
    assert "R8 liquidity" in failed(check(entry(), bars=trend(volume=1000.0)))
    assert "R8 trend filter" in failed(check(entry(), bars=trend(n=150)))


def test_r9_no_entry_a_day_before_results():
    v = check(entry(), today=date(2026, 11, 12), results_date=date(2026, 11, 13))
    assert failed(v) == {"R9 results blackout"}
    assert check(entry(), today=date(2026, 11, 12), results_date=date(2026, 11, 18)).passed
    unknown = check(entry(), results_date=None)
    assert unknown.passed and any("results date unknown" in w for w in unknown.warnings)


def test_r1_an_entry_without_a_stop_is_blocked():
    naked = Order("RELIANCE", "buy", 10, 1000.0, None, 1100.0, "Energy")
    assert "R1 stop and target" in failed(check(naked))


def test_exits_are_never_blocked_but_shorts_are():
    held = [Position("RELIANCE", 30, 1000.0, 960.0, "Energy")]
    broke = book(400_000, week=500_000, day=450_000, positions=held)
    out = check(entry(qty=30, side="sell"), broke, kill_switch_latched=True)
    assert out.passed
    assert not check(entry(qty=31, side="sell"), broke).passed


def test_risk_is_measured_from_a_higher_fresh_price():
    # 66 x (1000 - 930 x 0.995) = 4,927 at the stop with the gap: just inside 1% of 500,000.
    assert "R2 risk per trade" in failed(check(entry(qty=67, price=1000.0, stop=930.0, target=1200.0)))
    ok = entry(qty=66, price=1000.0, stop=930.0, target=1200.0)
    assert check(ok).passed
    assert "R2 risk per trade" in failed(check(ok, last_price=1010.0))


def test_only_delivery_and_no_adding():
    assert not check(Order("RELIANCE", "buy", 10, 1000.0, 960.0, 1080.0, "Energy", product="MIS")).passed
    held = [Position("RELIANCE", 10, 1000.0, 960.0, "Energy")]
    assert "adding" in failed(check(entry(), book(positions=held)))


def test_fingerprint_changes_with_the_order():
    assert entry().fingerprint == entry().fingerprint != entry(qty=31).fingerprint


# --- the command line ---------------------------------------------------------------

def test_cli_plan_size_check_and_kill_switch(tmp_path, capsys):
    prices = tmp_path / "prices"
    prices.mkdir()
    b = trend(start="2025-09-01")
    b.to_csv(prices / "RELIANCE.csv", index_label="date")
    as_of = f"{b.index[-1]:%Y-%m-%d}"
    data_cfg = tmp_path / "data.toml"
    data_cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"equity": 500000, "cash": 500000, "week_open_equity": 500000,
                              "day_open_equity": 500000, "positions": []}))
    state = tmp_path / "state"
    common = ["--data-config", str(data_cfg), "--state-dir", str(state)]

    assert risk_main([*common, "plan", "RELIANCE", "--as-of", as_of, "--out", str(tmp_path / "plan.json")]) == 0
    assert risk_main([*common, "size", str(tmp_path / "plan.json"), "--portfolio", str(pf),
                      "--out", str(tmp_path / "order.json")]) == 0
    order = json.loads((tmp_path / "order.json").read_text())
    assert order["sector"] == "Energy" and order["qty"] >= 1
    assert risk_main([*common, "check", str(tmp_path / "order.json"), "--portfolio", str(pf),
                      "--as-of", as_of, "--results-date", "2027-01-20"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert len((state / "risk_verdicts.jsonl").read_text().splitlines()) == 1

    pf.write_text(json.dumps({"equity": 470000, "cash": 470000, "week_open_equity": 500000,
                              "day_open_equity": 470000, "positions": []}))
    assert risk_main([*common, "check", str(tmp_path / "order.json"), "--portfolio", str(pf),
                      "--as-of", as_of]) == 1
    assert KillSwitch(state).latched
    assert risk_main([*common, "kill-switch"]) == 1
    assert risk_main([*common, "kill-switch", "--reset"]) == 2  # needs --by


def at_a_terminal(monkeypatch, typed="RESET"):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)


def test_kill_switch_reset_is_for_a_person_at_a_terminal(tmp_path, monkeypatch):
    state = tmp_path / "state"
    KillSwitch(state).latch("a -6% week", "2026-W44")
    common = ["--state-dir", str(state), "kill-switch", "--reset", "--by", "Harsha"]

    monkeypatch.setenv("CLAUDECODE", "1")                 # Claude Code's shell
    assert risk_main(common) == 1 and KillSwitch(state).latched
    with pytest.raises(ResetRefused):
        KillSwitch(state).reset("Claude")
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)  # piped, no terminal
    assert risk_main(common) == 1 and KillSwitch(state).latched

    at_a_terminal(monkeypatch, typed="yes")
    assert risk_main(common) == 1 and KillSwitch(state).latched
    at_a_terminal(monkeypatch)
    assert risk_main(common) == 0 and not KillSwitch(state).latched
    st = KillSwitch(state).state()
    assert (st["reset_by"], st["cleared_week"], st["cleared_reason"]) == ("Harsha", "2026-W44", "a -6% week")


def test_kill_switch_latches_on_the_drawdown_without_an_order(tmp_path):
    state = tmp_path / "state"
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"equity": 470000, "cash": 470000, "week_open_equity": 500000,
                              "day_open_equity": 470000, "positions": []}))
    assert risk_main(["--state-dir", str(state), "kill-switch", "--portfolio", str(pf),
                      "--as-of", "2026-10-28"]) == 1
    assert KillSwitch(state).state()["week"] == "2026-W44"


def test_a_sell_check_latches_the_kill_switch_too():
    held = [Position("RELIANCE", 30, 1000.0, 960.0, "Energy")]
    v = check(entry(qty=30, side="sell"), book(470_000, week=500_000, positions=held))
    assert v.passed and v.trip_kill_switch


def test_a_cleared_week_does_not_latch_again(tmp_path, monkeypatch):
    switch = KillSwitch(tmp_path)
    assert switch.latch("a -6% week", "2026-W44")
    at_a_terminal(monkeypatch)
    switch.reset("Harsha")
    assert not switch.latch("still down", "2026-W44") and not switch.latch("older", "2026-W43")
    assert switch.latch("a new week's breach", "2026-W45") and switch.latched


# --- the paper ticket ---------------------------------------------------------------

def passed(order, at="2026-10-26 10:15", status="PASS"):
    return {"at": at, "status": status, "fingerprint": order.fingerprint}


def test_ticket_copies_the_passed_order_and_uses_a_stop_loss_market_exit():
    o = entry(qty=30, price=1000.0, stop=960.0, target=1080.0)
    t = make_ticket(o, passed(o), date(2026, 10, 26), CFG)
    assert t["paper_only"] and t["fingerprint"] == o.fingerprint
    assert t["entry"] == {"exchangeSegment": "NSE_EQ", "productType": "CNC", "tradingSymbol": "RELIANCE",
                          "securityId": None, "transactionType": "BUY", "orderType": "LIMIT",
                          "validity": "DAY", "quantity": 30, "price": 1000.0}
    x = t["exits"]
    assert x["orderFlag"] == "OCO" and x["orderType"] == "MARKET"
    assert x["target"]["triggerPrice"] == 1080.0 and x["target"]["quantity"] == 30
    # A stop-loss market: no limit price, so a gap down below the stop still sells (at the open).
    assert x["stop"]["orderType"] == "STOP_LOSS_MARKET" and x["stop"]["triggerPrice"] == 960.0
    assert "price" not in x["stop"]
    assert t["loss_at_stop"] == 1200.0 and t["loss_with_gap"] == 1344.0  # 960 less 0.5% = 955.20


@pytest.mark.parametrize("verdict, today, latched, words", [
    (None, "2026-10-26", False, "no verdict"),
    ("BLOCK", "2026-10-26", False, "BLOCK, not PASS"),
    ("PASS", "2026-10-27", False, "not 2026-10-27"),
    ("PASS", "2026-10-26", True, "kill switch"),
])
def test_ticket_is_refused_without_a_fresh_pass(verdict, today, latched, words):
    o = entry()
    v = passed(o, status=verdict) if verdict else None
    with pytest.raises(TicketRefused, match=words):
        make_ticket(o, v, date.fromisoformat(today), CFG, kill_switch_latched=latched)


def test_ticket_refuses_sells_and_orders_without_exits():
    sell = entry(side="sell")
    with pytest.raises(TicketRefused, match="long entries only"):
        make_ticket(sell, passed(sell), date(2026, 10, 26), CFG)
    bare = entry(stop=None)
    with pytest.raises(TicketRefused, match="no stop"):
        make_ticket(bare, passed(bare), date(2026, 10, 26), CFG)


def test_cli_ticket_follows_the_latest_verdict_for_the_order(tmp_path, capsys):
    o = entry()
    (tmp_path / "order.json").write_text(json.dumps(o.to_json()))
    state = tmp_path / "state"
    state.mkdir()
    log = state / "risk_verdicts.jsonl"
    other = entry(qty=31)
    log.write_text("\n".join(json.dumps(v) for v in (passed(o), passed(other, status="BLOCK"))) + "\n")
    args = ["--state-dir", str(state), "ticket", str(tmp_path / "order.json"), "--as-of", "2026-10-26"]

    assert risk_main([*args, "--out", str(tmp_path / "ticket.json")]) == 0
    assert "PAPER  buy 30 RELIANCE" in capsys.readouterr().out
    assert json.loads((tmp_path / "ticket.json").read_text())["entry"]["quantity"] == 30

    with log.open("a") as fh:  # a later BLOCK of the same order wins
        fh.write(json.dumps(passed(o, at="2026-10-26 14:00", status="BLOCK")) + "\n")
    assert risk_main(args) == 1
    assert "not PASS" in capsys.readouterr().err
