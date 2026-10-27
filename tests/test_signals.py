import json
from datetime import date

import pandas as pd
import pytest

from swingbot.models import IST, Sentiment, Signal
from swingbot.risk import KillSwitch, NSECalendar, Portfolio, Position, RiskConfig, SignalRules
from swingbot.risk.cli import main as risk_main
from swingbot.risk.signals import Call, decide, entry_session, fresh_since, hold_plan, read_calls, technical_overlay

from test_risk import book, trend

CAL = NSECalendar.load("config/nse_holidays.toml")
SECTORS = {"RELIANCE": "Energy", "TCS": "IT", "INFY": "IT", "HDFCBANK": "Banks", "ITC": "FMCG"}
UP = trend(step=0.0015, seed=6)             # above its 200-day SMA, RSI about 59
AS_OF = pd.Timestamp(f"{UP.index[-1]:%Y-%m-%d} 16:00", tz=IST)  # after that day's close
GOOD_DATES = {t: date(2027, 1, 20) for t in SECTORS}


def call(ticker="RELIANCE", sentiment="bullish", confidence=0.8, horizon="72h", made_at=AS_OF, ref=None, score=0.5):
    return Call(ref or f"{ticker}-{confidence}", Signal(ticker, Sentiment(sentiment), confidence, horizon, score),
                made_at)


def run(calls, portfolio=None, bars=None, mood=None, rules=None, **kw):
    bars = bars or {}
    kw.setdefault("results_dates", GOOD_DATES)
    return decide(calls, as_of=AS_OF, portfolio=portfolio or book(), fetch_bars=lambda t: bars.get(t, UP),
                  rules=rules or SignalRules(), mood=mood, sectors=SECTORS, calendar=CAL, **kw)


def by_ref(decisions):
    return {d.ref: d for d in decisions}


# --- step 1: the call itself -------------------------------------------------------

@pytest.mark.parametrize("kw, words", [
    ({"sentiment": "bearish"}, "long-only"),
    ({"sentiment": "neutral"}, "no trade"),
    ({"confidence": 0.55}, "under 0.60"),
    ({"horizon": "48h"}, "outside the swing window"),
    ({"horizon": "30d"}, "outside the swing window"),
    ({"made_at": AS_OF - pd.Timedelta(hours=40)}, "already in the price"),
    ({"made_at": AS_OF + pd.Timedelta(hours=1)}, "the future"),
])
def test_calls_that_are_not_long_swing_trades_are_skipped_before_any_data(kw, words):
    fetched = []
    [d] = decide([call(**kw)], as_of=AS_OF, portfolio=book(), fetch_bars=fetched.append, calendar=CAL)
    assert (d.status, d.stage) == ("skipped", "call")
    assert words in d.reasons[0]
    assert fetched == []


def test_a_defensive_market_can_raise_the_confidence_bar():
    calls = [call("RELIANCE", confidence=0.7), call("TCS", confidence=0.8)]
    strict = SignalRules(defensive_min_confidence=0.75)
    d = by_ref(run(calls, mood={"verdict": "defensive", "as_of": "x"}, rules=strict))
    assert d["RELIANCE-0.7"].status == "skipped" and "defensive market" in d["RELIANCE-0.7"].reasons[0]
    assert d["TCS-0.8"].status == "ticket"
    assert by_ref(run(calls, mood={"verdict": "neutral"}, rules=strict))["RELIANCE-0.7"].status == "ticket"
    assert by_ref(run(calls, mood={"verdict": "defensive", "as_of": "x"}))["RELIANCE-0.7"].status == "ticket"


def test_an_unknown_mood_is_a_note_or_a_skip_never_neutral():
    [d] = run([call()])
    assert d.status == "ticket" and any("mood not checked" in n for n in d.notes)  # the gate's note
    [d] = run([call()], mood={"verdict": "unknown"}, rules=SignalRules(skip_when_mood_unknown=True))
    assert d.status == "skipped" and "unknown" in d.reasons[0]


def test_unreadable_rows_are_skipped_with_the_reason():
    [d] = run([Call("line 3", None, error="cannot read this call (KeyError: 'ticker')")])
    assert (d.status, d.ref) == ("skipped", "line 3") and "KeyError" in d.reasons[0]


# --- step 2: one per stock, best first, a cap per run -------------------------------

def test_one_call_per_stock_and_the_most_confident_wins():
    d = by_ref(run([call(confidence=0.7), call(confidence=0.9)]))
    assert d["RELIANCE-0.9"].status == "ticket"
    assert d["RELIANCE-0.7"].stage == "batch" and "more confident" in d["RELIANCE-0.7"].reasons[0]


def test_at_most_max_new_orders_tickets_and_the_best_calls_get_them():
    calls = [call("RELIANCE", confidence=0.65), call("TCS", confidence=0.9), call("HDFCBANK", confidence=0.8)]
    d = by_ref(run(calls, rules=SignalRules(max_new_orders=2)))
    assert d["TCS-0.9"].status == d["HDFCBANK-0.8"].status == "ticket"
    assert d["RELIANCE-0.65"].stage == "batch" and "max_new_orders 2" in d["RELIANCE-0.65"].reasons[0]


# --- step 3: technical overlay ------------------------------------------------------

def test_overlay_skips_downtrends_overbought_and_short_history():
    down = trend(step=-0.002, seed=6)
    hot = trend(step=0.0015, seed=4)  # RSI about 88
    bars = {"TCS": down, "INFY": hot, "ITC": UP.iloc[-120:]}
    d = by_ref(run([call("TCS"), call("INFY"), call("ITC")], bars=bars))
    assert "200-day SMA" in d["TCS-0.8"].reasons[0] and d["TCS-0.8"].stage == "technicals"
    assert "overbought" in " ".join(d["INFY-0.8"].reasons)
    assert "needs 200 sessions" in d["ITC-0.8"].reasons[0]
    assert all(x.verdict is None for x in d.values())  # never reached the gate


def test_overlay_flags_a_stock_stretched_far_above_its_ema10():
    why, facts = technical_overlay(UP, SignalRules(max_extension_atr=0.0, max_rsi=100))
    assert facts["atr_above_ema10"] > 0 and "chasing" in why[0]


def test_a_failed_price_fetch_skips_the_call():
    def boom(ticker):
        raise FileNotFoundError(f"no file for {ticker}")
    [d] = decide([call()], as_of=AS_OF, portfolio=book(), fetch_bars=boom, calendar=CAL)
    assert d.stage == "technicals" and "no price history" in d.reasons[0]


# --- steps 4 and 5: plan, size, gate, ticket -----------------------------------------

def test_a_good_call_becomes_a_paper_ticket_with_a_time_exit(tmp_path):
    log, switch = tmp_path / "risk_verdicts.jsonl", KillSwitch(tmp_path)
    [d] = run([call(horizon="72h")], verdict_log=log, kill_switch=switch)
    assert d.status == "ticket", d.text()
    t = d.ticket
    assert t["paper_only"] and t["entry"]["transactionType"] == "BUY" and t["entry"]["productType"] == "CNC"
    assert t["exits"]["orderFlag"] == "OCO" and t["exits"]["stop"]["triggerPrice"] == d.order["stop"]
    assert t["fingerprint"] == d.verdict["fingerprint"]
    # Fri 28 Aug 2026 after the close: enters Mon 31 Aug, 3 sessions counting that day.
    assert t["hold"] == {"max_sessions": 3, "entry_session": "2026-08-31", "exit_by": "2026-09-02",
                         "rule": "if neither the stop nor the target has filled, sell at the close of exit_by"}
    assert t["call"]["confidence"] == 0.8
    logged = [json.loads(x) for x in log.read_text().splitlines()]
    assert [(v["status"], v["fingerprint"]) for v in logged] == [("PASS", t["fingerprint"])]


def test_later_calls_are_sized_against_the_cash_earlier_tickets_used():
    # 250k equity: each ticket is capped at 15% notional, so cash shrinks with every one.
    small = book(equity=250_000)
    calls = [call("RELIANCE", confidence=0.9), call("TCS", confidence=0.8), call("HDFCBANK", confidence=0.7)]
    ds = run(calls, portfolio=small)
    tickets = [d for d in ds if d.status == "ticket"]
    assert len(tickets) == 3
    spent = sum(d.ticket["amount"] for d in tickets)
    assert spent <= 250_000
    # Cash for one ticket: the others only get the scraps, under the minimum order.
    tight = Portfolio(250_000, 40_000, 250_000, 250_000)
    d = by_ref(run(calls, portfolio=tight))
    assert d["RELIANCE-0.9"].status == "ticket"
    assert d["TCS-0.8"].stage == "size" and "under the 25,000 minimum (decided by cash)" in d["TCS-0.8"].reasons[0]
    assert d["HDFCBANK-0.7"].stage == "size" and "minimum" in d["HDFCBANK-0.7"].reasons[0]
    assert by_ref(run(calls, portfolio=tight, rules=SignalRules(min_order_value=0)))["TCS-0.8"].status == "ticket"


def test_the_gate_blocks_and_the_reason_is_kept(tmp_path):
    held = book(positions=[Position("RELIANCE", 10, 150.0, 140.0, "Energy")])
    log = tmp_path / "v.jsonl"
    [d] = run([call()], portfolio=held, verdict_log=log)
    assert (d.status, d.stage) == ("blocked", "gate")
    assert any(r.startswith("adding:") for r in d.reasons)
    assert json.loads(log.read_text())["status"] == "BLOCK"


def test_an_unknown_sector_or_results_inside_the_blackout_is_blocked():
    [d] = decide([call("NEWCO")], as_of=AS_OF, portfolio=book(), fetch_bars=lambda t: UP, calendar=CAL,
                 results_dates={})
    assert d.status == "blocked" and any("R4 sector" in r for r in d.reasons)
    [d] = run([call()], results_dates={"RELIANCE": date(2026, 8, 31)})
    assert d.status == "blocked" and any("R9 results blackout" in r for r in d.reasons)


def test_a_latched_kill_switch_blocks_every_entry(tmp_path):
    switch = KillSwitch(tmp_path)
    switch.latch("test")
    ds = run([call("RELIANCE"), call("TCS")], kill_switch=switch, verdict_log=tmp_path / "v.jsonl")
    assert {d.status for d in ds} == {"blocked"}
    assert all(any("R5" in r for r in d.reasons) for d in ds)


def test_a_weekly_breach_found_on_the_first_call_latches_and_blocks_the_rest(tmp_path):
    switch = KillSwitch(tmp_path)
    down_week = book(equity=470_000, week=500_000, day=470_000)
    ds = run([call("RELIANCE", confidence=0.9), call("TCS")], portfolio=down_week, kill_switch=switch,
             verdict_log=tmp_path / "v.jsonl")
    assert switch.latched and {d.status for d in ds} == {"blocked"}


def test_without_a_verdict_log_nothing_is_written_or_latched(tmp_path):
    switch = KillSwitch(tmp_path)
    run([call()], portfolio=book(equity=470_000, week=500_000, day=470_000), kill_switch=switch)
    assert not switch.latched and list(tmp_path.iterdir()) == []


# --- timing ------------------------------------------------------------------------------

@pytest.mark.parametrize("when, expected", [
    ("2026-11-02 08:30", "2026-11-02"),  # Monday before the open: that day
    ("2026-11-02 10:00", "2026-11-03"),  # during the session: the next one
    ("2026-11-06 16:00", "2026-11-09"),  # Friday evening: Monday
])
def test_entry_session(when, expected):
    assert entry_session(pd.Timestamp(when, tz=IST), NSECalendar()) == date.fromisoformat(expected)


def test_hold_skips_weekends_and_holidays():
    cal = NSECalendar({2026: {date(2026, 11, 4)}})
    h = hold_plan(Signal("X", Sentiment.BULLISH, 0.8, "5d"), pd.Timestamp("2026-11-02 16:00", tz=IST), cal)
    # Tue 3 (1), Wed 4 holiday, Thu 5 (2), Fri 6 (3), Mon 9 (4), Tue 10 (5)
    assert (h["entry_session"], h["exit_by"], h["max_sessions"]) == ("2026-11-03", "2026-11-10", 5)


# --- reading calls and rules --------------------------------------------------------------

def test_read_calls_from_jsonl_json_and_csv(tmp_path):
    jl = tmp_path / "s.jsonl"
    jl.write_text('{"ticker": "tcs", "sentiment": "Bullish", "confidence": 0.8, "time_horizon": "72h", '
                  '"as_of": "2026-08-28 15:45", "event_id": "e1"}\n\nnot json\n'
                  '{"ticker": "INFY", "sentiment": "up", "confidence": 0.9}\n')
    a, b, c = read_calls(jl)
    assert (a.ref, a.signal.ticker, a.signal.sentiment, str(a.made_at)) == \
        ("e1", "TCS", Sentiment.BULLISH, "2026-08-28 15:45:00+05:30")
    assert b.signal is None and "not JSON" in b.error and b.ref == "line 3"
    assert c.signal is None and "cannot read" in c.error

    js = tmp_path / "s.json"
    js.write_text(json.dumps([{"ticker": "ITC", "sentiment": "bullish", "confidence": 0.7}]))
    [d] = read_calls(js)
    assert d.signal.time_horizon == "72h" and d.made_at is None

    cs = tmp_path / "s.csv"
    cs.write_text("ticker,sentiment,confidence,time_horizon\nLT,bullish,0.75,5d\nLT,bullish,2,5d\n")
    ok, bad = read_calls(cs)
    assert ok.signal.horizon_hours == 120 and bad.signal is None and bad.ref == "row 3"


@pytest.mark.parametrize("made_at, fresh", [
    ("2026-08-28 09:20", True),    # Friday's session, entry Monday
    ("2026-08-28 09:10", False),   # before Friday's open: Thursday's news
])
def test_only_news_since_the_last_session_before_the_entry_is_fresh(made_at, fresh):
    [d] = run([call(made_at=pd.Timestamp(made_at, tz=IST))])
    assert (d.stage != "call") == fresh, d.text()


@pytest.mark.parametrize("as_of, sessions, since", [
    ("2026-08-31 08:30", 1, "2026-08-28 09:15"),  # Monday before the open: since Friday's open, weekend included
    ("2026-08-31 16:00", 1, "2026-08-31 09:15"),  # Monday evening, entry Tuesday: since Monday's open
    ("2026-08-31 16:00", 2, "2026-08-28 09:15"),
    ("2026-11-05 16:00", 1, "2026-11-05 09:15"),  # entry Friday 6th
])
def test_fresh_since(as_of, sessions, since):
    assert fresh_since(pd.Timestamp(as_of, tz=IST), sessions, NSECalendar()) == pd.Timestamp(since, tz=IST)


def test_rules_load_and_refuse_a_lower_bar_in_a_defensive_market(tmp_path):
    rules = SignalRules.load("config/signals.toml")
    assert rules == SignalRules()  # the file and the built-in defaults agree
    with pytest.raises(ValueError, match="only raise"):
        SignalRules(min_confidence=0.7, defensive_min_confidence=0.6)
    p = tmp_path / "r.toml"
    p.write_text("[filters]\nmin_confidance = 0.5\n")
    with pytest.raises(ValueError, match="min_confidance"):
        SignalRules.load(p)


# --- the CLI --------------------------------------------------------------------------------

def test_cli_orders_writes_decisions_orders_and_tickets(tmp_path, capsys):
    prices = tmp_path / "prices"
    prices.mkdir()
    for t in ("RELIANCE", "TCS"):
        UP.to_csv(prices / f"{t}.csv", index_label="date")
    data_cfg = tmp_path / "data.toml"
    data_cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"equity": 500000, "cash": 500000, "week_open_equity": 500000,
                              "day_open_equity": 500000, "positions": []}))
    sig = tmp_path / "signals.jsonl"
    sig.write_text("\n".join(json.dumps(x) for x in [
        {"ticker": "RELIANCE", "sentiment": "bullish", "confidence": 0.8, "time_horizon": "72h", "event_id": "e1"},
        {"ticker": "TCS", "sentiment": "bearish", "confidence": 0.9, "time_horizon": "72h", "event_id": "e2"},
    ]) + "\n")
    dates = tmp_path / "results.csv"
    dates.write_text("symbol,date\nRELIANCE,2027-01-20\n")
    state, out = tmp_path / "state", tmp_path / "orders"
    as_of = f"{UP.index[-1]:%Y-%m-%d} 16:00"
    args = ["--data-config", str(data_cfg), "--state-dir", str(state), "orders", str(sig), "--portfolio", str(pf),
            "--results-dates", str(dates), "--as-of", as_of, "--out-dir", str(out)]

    assert risk_main([*args, "--no-write"]) == 0
    assert "1 tickets" in capsys.readouterr().out and not state.exists() and not out.exists()

    assert risk_main(args) == 0
    text = capsys.readouterr().out
    assert "TICKET   RELIANCE" in text and "long-only" in text and "nothing was sent" in text
    day = out / f"{UP.index[-1]:%Y-%m-%d}"
    decisions = json.loads((day / "decisions.json").read_text())["decisions"]
    assert [d["status"] for d in decisions] == ["skipped", "ticket"]
    order = json.loads((day / "RELIANCE.order.json").read_text())
    assert order["sector"] == "Energy" and order["qty"] >= 1
    assert json.loads((day / "RELIANCE.ticket.json").read_text())["paper_only"] is True

    # The order file is the one swingbot-risk ticket and swingbot-paper fill read: its PASS is in the log.
    assert risk_main(["--state-dir", str(state), "ticket", str(day / "RELIANCE.order.json"), "--as-of", as_of]) == 0


# --- the daily loop's hook: sized orders, no gate ------------------------------------------

def test_without_the_gate_calls_stop_at_a_sized_order_and_nothing_is_logged(tmp_path):
    switch = KillSwitch(tmp_path)
    switch.latch("test")  # the gate would block; without it the order is still sized
    calls = [call("RELIANCE", confidence=0.9), call("TCS", confidence=0.8), call("HDFCBANK", sentiment="bearish")]
    d = by_ref(run(calls, gate=False, kill_switch=switch))
    assert d["RELIANCE-0.9"].status == d["TCS-0.8"].status == "sized"
    assert d["RELIANCE-0.9"].verdict is None and d["RELIANCE-0.9"].ticket is None
    assert d["RELIANCE-0.9"].order["plan"]["hold"]["max_sessions"] == 3
    assert d["HDFCBANK-0.8"].status == "skipped"
    assert [p.name for p in tmp_path.iterdir()] == ["kill_switch.json"]


def test_daily_hook_returns_sized_orders_and_writes_the_decisions(tmp_path):
    from types import SimpleNamespace

    from swingbot.data.router import DataConfig
    from swingbot.risk import Order
    from swingbot.risk.signals import daily_hook

    prices = tmp_path / "prices"
    prices.mkdir()
    UP.to_csv(prices / "RELIANCE.csv", index_label="date")
    cfg = tmp_path / "data.toml"
    cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    day = SimpleNamespace(as_of=AS_OF, risk=RiskConfig(), data=DataConfig.load(cfg), calendar=CAL,
                          stamp=f"{AS_OF:%Y-%m-%d %H:%M}", path=lambda name: tmp_path / "day" / name)
    signals = [
        {"event_id": "e1", "ticker": "RELIANCE", "published_at": f"{AS_OF - pd.Timedelta(hours=2)}",
         "headline": "h", "sentiment": "bullish", "confidence": 0.8, "score": 0.6, "rationale": "r"},
        {"event_id": "e2", "ticker": "TCS", "published_at": f"{AS_OF}", "headline": "h",
         "sentiment": "bearish", "confidence": 0.9, "score": -0.6, "rationale": "r"},
    ]
    orders = daily_hook(day, signals, book(), None)
    assert [type(o) for o in orders] == [Order]
    o = orders[0]
    assert (o.ticker, o.side, o.product, o.sector) == ("RELIANCE", "buy", "CNC", "Energy")
    assert o.stop < o.entry < o.target and o.plan["call"] == "e1"
    saved = json.loads((tmp_path / "day" / "signal_decisions.json").read_text())["decisions"]
    assert {d["ref"]: d["status"] for d in saved} == {"e1": "sized", "e2": "skipped"}
