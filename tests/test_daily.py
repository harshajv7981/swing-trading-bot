import json
from datetime import date

import pandas as pd
import pytest

from swingbot.daily import DailyConfig, Day, finish, news_window, phase, previous_session, start
from swingbot.daily.cli import main as daily_main
from swingbot.daily.hooks import OK, Step
from swingbot.memory import MemoryStore
from swingbot.paper import Ledger
from swingbot.risk import KillSwitch, NSECalendar, Order

CAL = NSECalendar({2026: {date(2026, 11, 10)}})
MORNING = "2026-11-03 08:30"   # a Tuesday, before the open
PERSONAS = ("Retail Bull", "Institutional Bear", "Foreign Institutional Investor", "Financial Media")


def frame(closes, end="2026-11-02"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    df = pd.DataFrame({"close": closes}, index=idx)
    df["open"], df["high"], df["low"] = df["close"] - 0.5, df["close"] + 1, df["close"] - 1
    df["volume"] = 1_000_000.0
    return df[["open", "high", "low", "close", "volume"]].rename_axis("date")


@pytest.fixture
def repo(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    up = [100 + i * 0.4 for i in range(260)]
    for t in ("ABC", "NIFTY50"):
        frame(up).to_csv(prices / f"{t}.csv")
    (tmp_path / "events.csv").write_text(
        "event_id,ticker,published_at,headline,body,source\n"
        "old,ABC,2026-10-30 18:00+05:30,ABC old news,,test\n"
        "e1,ABC,2026-11-02 18:00+05:30,ABC wins a large order,,test\n")
    (tmp_path / "data.toml").write_text(
        f'[sources]\nprices = "csv"\nfundamentals = "nse_results"\n'
        f'[paths]\nprices_dir = "{prices}"\nevents_file = "{tmp_path / "events.csv"}"\n'
        "[options]\ncache_prices = false\n")
    (tmp_path / "holidays.toml").write_text('[holidays]\n2026 = ["2026-11-10"]\n')
    return tmp_path


def config(repo, **kw):
    return DailyConfig(runs_dir=repo / "runs", ledger=repo / "ledger.jsonl", state_dir=repo / "state",
                       memory_db=repo / "memory.db", holidays=repo / "holidays.toml",
                       data_config=str(repo / "data.toml"), **kw)


class Hooks:
    """Records what the loop handed each hook."""

    def __init__(self, orders=()):
        self.calls, self.orders, self.tickets, self.signals = [], list(orders), None, None

    def make(self):
        def step(name):
            def hook(day, *args):
                self.calls.append(name)
                return Step(name, OK, "fake")
            return hook

        def signal_to_order(day, signals, portfolio, mood):
            self.calls.append("signal_to_order")
            self.signals = signals
            return self.orders

        def broker_submit(day, tickets):
            self.calls.append("broker_submit")
            self.tickets = tickets
            return Step("paper broker submit", OK, f"{len(tickets)} held")

        return {"refresh": step("refresh"), "broker_settle": step("broker_settle"),
                "signal_to_order": signal_to_order, "broker_submit": broker_submit}


def open_account(repo, cash=1_000_000):
    Ledger(repo / "ledger.jsonl").append({"kind": "open", "at": "2026-10-01 09:00", "cash": cash})


def write_views(day, stance=0.7):
    with day.path("views.jsonl").open("w") as fh:
        for p in PERSONAS:
            fh.write(json.dumps({"event_id": "e1", "persona": p, "round": 1, "stance": stance,
                                 "conviction": 0.8, "rationale": "a big order"}) + "\n")


def good_order():
    return Order("ABC", "buy", 400, entry=203.6, stop=195.0, target=221.0, sector="Tech")


# --- hours and the calendar -------------------------------------------------


def test_phases_and_sessions():
    assert phase("2026-11-03 08:59", CAL) == "pre_open"
    assert phase("2026-11-03 09:15", CAL) == "open"
    assert phase("2026-11-03 15:30", CAL) == "closed"
    assert phase("2026-11-10 08:00", CAL) == "holiday"
    assert phase("2026-11-07 08:00", CAL) == "holiday"           # a Saturday
    assert previous_session(date(2026, 11, 11), CAL) == date(2026, 11, 9)
    assert previous_session(date(2026, 11, 9), CAL) == date(2026, 11, 6)


def test_news_window_starts_at_the_previous_open():
    since, until = news_window("2026-11-03 08:30", CAL)
    assert f"{since:%Y-%m-%d %H:%M}" == "2026-11-02 09:08"
    assert f"{until:%Y-%m-%d %H:%M}" == "2026-11-03 08:30"


# --- the whole day ------------------------------------------------------------


def test_full_day_gates_the_order_and_hands_the_ticket_to_the_broker(repo):
    open_account(repo)
    hooks = Hooks([good_order()])
    day = Day(MORNING, config(repo), hooks.make())
    state = start(day)
    assert state["entries"], state["why_not"]
    assert hooks.calls == ["refresh", "broker_settle"]
    brief = day.path("brief.md").read_text()
    assert "### e1 · ABC" in brief and "### old" not in brief   # older news only as context

    write_views(day)
    later = Day("2026-11-03 08:50", config(repo), hooks.make())
    result = finish(later)
    assert [s["ticker"] for s in hooks.signals] == ["ABC"] and hooks.signals[0]["sentiment"] == "bullish"
    assert len(hooks.tickets) == 1 and hooks.tickets[0]["entry"]["quantity"] == 400
    assert result["entries"] and len(result["tickets"]) == 1
    verdicts = (repo / "state" / "risk_verdicts.jsonl").read_text().splitlines()
    assert json.loads(verdicts[-1])["status"] == "PASS"
    link = json.loads((repo / "state" / "daily_entries.jsonl").read_text())
    assert link["signal"]["event_id"] == "e1" and link["fingerprint"] == good_order().fingerprint
    assert "Paper tickets" in later.path("summary.md").read_text()


def test_a_second_order_is_checked_against_the_first(repo):
    open_account(repo)
    # The first PASSes; the second sees the first as held, and adding is off.
    orders = [good_order(), Order("ABC", "buy", 400, entry=203.6, stop=195.0, target=221.0, sector="Tech")]
    hooks = Hooks(orders)
    cfg = config(repo)
    start(Day(MORNING, cfg, hooks.make()))
    write_views(Day(MORNING, cfg))
    result = finish(Day("2026-11-03 08:50", cfg, hooks.make()))
    gates = [s for s in result["steps"] if s["name"] == "gate ABC"]
    assert [g["status"] for g in gates] == ["ok", "blocked"]
    assert "adding" in gates[-1]["detail"]


def test_a_bearish_day_makes_no_tickets(repo):
    open_account(repo)
    hooks = Hooks([])
    cfg = config(repo)
    start(Day(MORNING, cfg, hooks.make()))
    write_views(Day(MORNING, cfg), stance=-0.7)
    result = finish(Day("2026-11-03 08:50", cfg, hooks.make()))
    assert hooks.signals[0]["sentiment"] == "bearish"
    assert result["tickets"] == [] and hooks.tickets == []


# --- when there are no new entries --------------------------------------------


def test_holiday_settles_and_reports_but_runs_no_swarm(repo):
    open_account(repo)
    hooks = Hooks([good_order()])
    cfg = config(repo)
    state = start(Day("2026-11-10 08:30", cfg, hooks.make()))
    assert not state["entries"] and "not an NSE session" in state["why_not"][0]
    assert hooks.calls == ["refresh", "broker_settle"]
    assert not (cfg.runs_dir / "2026-11-10" / "brief.md").exists()
    result = finish(Day("2026-11-10 09:00", cfg, hooks.make()))
    assert "signal_to_order" not in hooks.calls and "broker_submit" not in hooks.calls
    assert any(s["name"] == "ledger report" for s in result["steps"])


def test_a_year_with_no_holiday_list_places_no_entries(repo):
    open_account(repo)
    (repo / "holidays.toml").write_text('[holidays]\n2025 = ["2025-12-25"]\n')
    state = start(Day(MORNING, config(repo), Hooks([good_order()]).make()))
    assert not state["entries"] and any("no NSE holiday list for 2026" in w for w in state["why_not"])


def test_after_the_close_no_entries(repo):
    open_account(repo)
    hooks = Hooks([good_order()])
    cfg = config(repo)
    start(Day(MORNING, cfg, hooks.make()))
    write_views(Day(MORNING, cfg))
    result = finish(Day("2026-11-03 16:00", cfg, hooks.make()))
    assert not result["entries"] and "session is over" in result["why_not"][0]
    assert "signal_to_order" not in hooks.calls


def test_a_latched_kill_switch_stops_entries_and_is_never_reset(repo):
    open_account(repo)
    KillSwitch(repo / "state").latch("test")
    hooks = Hooks([good_order()])
    cfg = config(repo)
    state = start(Day(MORNING, cfg, hooks.make()))
    assert not state["entries"] and "kill switch" in state["why_not"][0]
    finish(Day("2026-11-03 08:50", cfg, hooks.make()))
    assert "signal_to_order" not in hooks.calls
    assert KillSwitch(repo / "state").latched


def test_start_latches_the_kill_switch_on_a_weekly_loss(repo):
    open_account(repo)
    closes = [200.0] * 259 + [180.0]                      # Monday's close drops 10%
    frame(closes).to_csv(repo / "prices" / "XYZ.csv")
    Ledger(repo / "ledger.jsonl").append({"kind": "fill", "at": "2026-10-28 09:20", "ticker": "XYZ",
                                          "side": "buy", "qty": 3000, "price": 200.0, "costs": 0,
                                          "stop": 150.0, "target": 260.0, "sector": "Tech", "verdict": "PASS"})
    state = start(Day(MORNING, config(repo), Hooks().make()))
    assert KillSwitch(repo / "state").latched
    assert not state["entries"]


def test_no_account_means_no_entries(repo):
    state = start(Day(MORNING, config(repo), Hooks().make()))
    assert not state["entries"] and "no paper account" in state["why_not"][0]


def test_finish_needs_start(repo):
    with pytest.raises(FileNotFoundError):
        finish(Day(MORNING, config(repo), Hooks().make()))


# --- the Verifier -----------------------------------------------------------------


def test_closed_trades_go_to_memory_with_their_news(repo):
    open_account(repo)
    ledger = Ledger(repo / "ledger.jsonl")
    ledger.append({"kind": "fill", "at": "2026-10-20 09:20", "ticker": "ABC", "side": "buy", "qty": 100,
                   "price": 190.0, "costs": 0, "stop": 180.0, "target": 210.0, "sector": "Tech",
                   "verdict": "PASS", "fingerprint": "fp1"})
    ledger.append({"kind": "fill", "at": "2026-10-27 10:00", "ticker": "ABC", "side": "sell", "qty": 100,
                   "price": 205.0, "costs": 0})
    (repo / "state").mkdir()
    (repo / "state" / "daily_entries.jsonl").write_text(json.dumps({
        "fingerprint": "fp1", "day": "2026-10-20", "ticker": "ABC",
        "signal": {"event_id": "n9", "ticker": "ABC", "published_at": "2026-10-19T18:00:00+05:30",
                   "headline": "ABC raises guidance", "sentiment": "bullish", "confidence": 0.7,
                   "score": 0.5, "rationale": "guidance"}}) + "\n")
    cfg = config(repo)
    start(Day("2026-11-10 08:30", cfg, Hooks().make()))
    finish(Day("2026-11-10 08:40", cfg, Hooks().make()))
    store = MemoryStore(cfg.memory_db)
    [row] = store.trades()
    store.close()
    assert row["trade_id"] == "paper:n9" and row["mode"] == "paper" and row["headline"] == "ABC raises guidance"
    assert row["hit"] == 1
    assert "ABC raises guidance" in (cfg.runs_dir / "2026-11-10" / "verify.md").read_text()


# --- the stubs and the CLI ------------------------------------------------------------


def test_stub_hooks_run_end_to_end_and_say_what_is_missing(repo):
    open_account(repo)
    cfg = config(repo)
    day = Day(MORNING, cfg)
    state = start(day)
    assert {s["status"] for s in state["steps"] if s["name"] == "paper broker settle"} == {"stub"}
    write_views(day)
    result = finish(Day("2026-11-03 08:50", cfg))
    assert result["tickets"] == []
    assert any("no signal-to-order step yet" in n for n in result["notes"])


def test_unknown_hook_in_config_is_refused(tmp_path):
    (tmp_path / "daily.toml").write_text('[hooks]\nrefersh = "x:y"\n')
    with pytest.raises(ValueError, match="unknown hooks"):
        DailyConfig.load(tmp_path / "daily.toml")


def test_cli_start_and_finish(repo, capsys):
    open_account(repo)
    (repo / "daily.toml").write_text(
        f'[paths]\nruns_dir = "{repo / "runs"}"\nledger = "{repo / "ledger.jsonl"}"\n'
        f'state_dir = "{repo / "state"}"\nmemory_db = "{repo / "memory.db"}"\n'
        f'holidays = "{repo / "holidays.toml"}"\ndata_config = "{repo / "data.toml"}"\n')
    assert daily_main(["--config", str(repo / "daily.toml"), "--as-of", MORNING, "start"]) == 0
    assert "Next: play the swarm" in capsys.readouterr().out
    write_views(Day(MORNING, DailyConfig.load(repo / "daily.toml")))
    assert daily_main(["--config", str(repo / "daily.toml"), "--as-of", "2026-11-03 08:50", "finish"]) == 0
    assert "Paper trading day 2026-11-03" in capsys.readouterr().out


def test_refresh_adapter_wraps_the_refresh_report(repo, monkeypatch):
    from swingbot.daily import adapters
    import swingbot.data.refresh as refresh

    seen = {}

    class Report:
        ok = False
        def text(self): return "prices: 3 updated, 1 failed"
        def failed(self): return ["X"]

    def fake_run(router, now=None, calendar=None, actions_dir=None):
        seen.update(router=router, now=now)
        return Report()

    monkeypatch.setattr(refresh, "run", fake_run)
    step = adapters.refresh(Day(MORNING, config(repo)))
    assert step.status == "failed" and "1 item(s) failed" in step.detail
    assert f"{seen['now']:%Y-%m-%d %H:%M}" == MORNING
    assert str(seen["router"].config.prices_dir) == str(repo / "prices")


def test_monday_trades_friday_evening_news_with_the_real_signal_hook(repo, monkeypatch):
    import swingbot.risk.signals as signals_mod
    from swingbot.daily import adapters

    monkeypatch.setattr(signals_mod, "load_sectors", lambda *a, **k: {"ABC": "Tech"})
    closes = [100 + i * 0.4 + (3 if i % 2 else -3) for i in range(260)]   # a choppy uptrend, RSI well under 75
    for t in ("ABC", "NIFTY50"):
        frame(closes, end="2026-11-06").to_csv(repo / "prices" / f"{t}.csv")
    (repo / "events.csv").write_text("event_id,ticker,published_at,headline,body,source\n"
                                     "e1,ABC,2026-11-06 18:00+05:30,ABC wins a large order,,test\n")
    open_account(repo)
    hooks = Hooks().make()
    hooks["signal_to_order"] = adapters.signal_to_order
    cfg = config(repo)
    start(Day("2026-11-09 08:30", cfg, hooks))
    write_views(Day("2026-11-09 08:30", cfg))
    finish(Day("2026-11-09 08:50", cfg, hooks))
    [d] = json.loads((cfg.runs_dir / "2026-11-09" / "signal_decisions.json").read_text())["decisions"]
    assert d["status"] == "sized", d["reasons"]


def test_alerts_go_out_once_for_a_kill_switch_latch_found_by_start(repo):
    open_account(repo, cash=1_000_000)
    got = []
    first = Day(MORNING, config(repo), Hooks().make())
    first.alert_sender = got.append
    start(first)                                    # first run: the welcome only
    assert [a.key for a in got] == ["hello"]

    KillSwitch(repo / "state").latch("equity fell 3.2% this week")
    later = Day("2026-11-03 08:50", config(repo), Hooks().make())
    later.alert_sender = got.append
    state = start(later)
    finish(later)                                   # already sent: not again
    assert [a.title for a in got] == ["Swing bot alerts are on", "KILL SWITCH LATCHED"]
    assert any(s["name"] == "alerts" and "1 sent" in s["detail"] for s in state["steps"])


def test_the_ticket_carries_the_time_exit_from_the_order(repo):
    open_account(repo)
    hold = {"max_sessions": 3, "entry_session": "2026-11-03", "exit_by": "2026-11-05"}
    order = Order("ABC", "buy", 400, entry=203.6, stop=195.0, target=221.0, sector="Tech", plan={"hold": hold})
    hooks = Hooks([order])
    start(Day(MORNING, config(repo), hooks.make()))
    write_views(Day(MORNING, config(repo), hooks.make()))
    finish(Day("2026-11-03 08:50", config(repo), hooks.make()))
    assert hooks.tickets[0]["hold"] == hold


def test_finish_survives_a_weekly_breach_in_the_ledger_history(repo):
    # The Verifier's replay used to look for a state_dir it was never given, and crash finish.
    open_account(repo)
    Ledger(repo / "ledger.jsonl").append({"kind": "fill", "at": "2026-10-05 09:15", "ticker": "ABC", "side": "buy",
                                          "qty": 1000, "price": 400.0, "stop": 380.0, "target": 440.0,
                                          "verdict": "PASS", "sector": "Tech"})
    start(Day(MORNING, config(repo), Hooks().make()))
    result = finish(Day("2026-11-03 08:50", config(repo), Hooks().make()))
    assert any(s["name"] == "trade verifier" for s in result["steps"])
    assert KillSwitch(repo / "state").latched


def test_start_says_when_no_results_dates_are_known(repo):
    open_account(repo)
    day = Day(MORNING, config(repo), Hooks().make())
    state = start(day)
    step = next(s for s in state["steps"] if s["name"] == "results dates")
    assert "none known" in step["detail"] and "blocks nothing" in step["detail"]
    assert any("results_dates.csv" in n for n in state["notes"])


def test_a_typed_results_date_blocks_the_entry_in_the_blackout(repo):
    open_account(repo)
    (repo / "state").mkdir()
    (repo / "state" / "results_dates.csv").write_text("symbol,date\nABC,2026-11-04\n")
    hooks = Hooks([good_order()])
    day = Day(MORNING, config(repo), hooks.make())
    state = start(day)
    step = next(s for s in state["steps"] if s["name"] == "results dates")
    assert step["detail"] == "1 company with a date ahead; in blackout today: ABC"
    write_views(day)
    result = finish(Day("2026-11-03 08:50", config(repo), hooks.make()))
    assert result["tickets"] == []
    gate = next(s for s in result["steps"] if s["name"] == "gate ABC")
    assert gate["status"] == "blocked" and "R9 results blackout" in gate["detail"]
