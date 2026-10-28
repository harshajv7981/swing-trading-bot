import json

import pytest
from test_daily import PERSONAS
from test_risk import trend

from swingbot.paper import Ledger
from swingbot.walkforward import Run, main


@pytest.fixture
def data(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    for t in ("TCS", "NIFTY50"):
        trend(n=315, step=0.0015, seed=6).to_csv(prices / f"{t}.csv", index_label="date")
    events = tmp_path / "events.csv"
    events.write_text(
        "event_id,ticker,published_at,headline,body,source\n"
        "old,TCS,2026-06-29 18:00+05:30,TCS news the model may remember,,test\n"
        "e1,TCS,2026-11-02 18:00+05:30,TCS wins a large order,,test\n"
        "e2,TCS,2026-11-05 18:00+05:30,TCS later news,,test\n")
    holidays = tmp_path / "holidays.toml"
    holidays.write_text('[holidays]\n2026 = ["2026-11-10"]\n')
    return tmp_path, prices, events, holidays


def views(path, event_id, stance):
    with path.open("a") as fh:
        for p in PERSONAS:
            fh.write(json.dumps({"event_id": event_id, "persona": p, "round": 1, "stance": stance,
                                 "conviction": 0.8, "rationale": "test"}) + "\n")


def test_walk_forward_stops_for_the_swarm_and_never_sees_later_news(data):
    tmp, prices, events, holidays = data
    run = Run(tmp / "wf")
    from datetime import date
    st = run.init(date(2026, 11, 2), date(2026, 11, 6), 1_000_000, prices, events, str(holidays))
    assert st["days"] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    said = []
    assert run.next(said.append) == "brief"           # Mon has no news since Fri 09:08; Tue has e1
    assert run.state()["days"][run.state()["index"]] == "2026-11-03"
    brief = (tmp / "wf/days/2026-11-03/brief.md").read_text()
    assert "### e1" in brief and "e2" not in brief and "### old" not in brief

    views(tmp / "wf/days/2026-11-03/views.jsonl", "e1", 0.7)
    assert run.next(said.append) == "brief"           # Tue finishes; Fri 6th has e2 (Thu evening news)
    tue = json.loads((tmp / "wf/days/2026-11-03/finish.json").read_text())
    assert [s["event_id"] for s in tue["signals"]] == ["e1"]
    views(tmp / "wf/days/2026-11-06/views.jsonl", "e2", -0.7)
    assert run.next(said.append) == "done"
    fills = [r for r in Ledger(tmp / "wf/ledger.jsonl").records() if r["kind"] == "fill"]
    # Monday evening's news is bought at Tuesday's open, never before it.
    assert [(r["ticker"], r["side"], r["at"]) for r in fills][:1] == [("TCS", "buy", "2026-11-03 09:15")]
    # A 72h call held from Tue: sold at Thu's close by the time exit, settled on Fri morning.
    tcs = [r for r in fills if r["ticker"] == "TCS"]
    assert [(r["side"], r["at"], r["reason"]) for r in tcs][1:] == [("sell", "2026-11-05 15:30", "time")]
    ticket = json.loads(next((tmp / "wf/days/2026-11-03/tickets").glob("*.json")).read_text())
    assert ticket["hold"]["exit_by"] == "2026-11-05"
    assert "walk-forward run done" in said[-1]
    assert "Sessions run: 5 of 5" in run.report()


def test_init_refuses_a_second_run_and_a_year_without_holidays(data, capsys):
    tmp, prices, events, holidays = data
    args = ["--dir", str(tmp / "wf"), "init", "--from", "2026-11-02", "--to", "2026-11-06", "--cash", "100000",
            "--prices", str(prices), "--events", str(events), "--holidays", str(holidays)]
    assert main(args) == 0
    assert main(args) == 2 and "already holds a run" in capsys.readouterr().err
    args[1], args[6] = str(tmp / "wf2"), "2027-01-08"
    assert main(args) == 2 and "2027" in capsys.readouterr().err


def test_results_dates_given_to_init_black_out_the_entry(data):
    tmp, prices, events, holidays = data
    dates = tmp / "results.csv"
    dates.write_text("symbol,date\nTCS,2026-08-12\nTCS,2026-11-04\n")
    args = ["--dir", str(tmp / "wf"), "init", "--from", "2026-11-02", "--to", "2026-11-06", "--cash", "1000000",
            "--prices", str(prices), "--events", str(events), "--holidays", str(holidays),
            "--results-dates", str(dates)]
    assert main(args) == 0
    assert (tmp / "wf/state/results_dates.csv").read_text() == dates.read_text()
    run = Run(tmp / "wf")
    assert run.next() == "brief"
    views(tmp / "wf/days/2026-11-03/views.jsonl", "e1", 0.7)
    run.next()
    tue = json.loads((tmp / "wf/days/2026-11-03/finish.json").read_text())
    assert tue["tickets"] == []
    assert any("R9 results blackout" in s["detail"] for s in tue["steps"] if s["name"] == "gate TCS")


def test_init_refuses_a_bad_results_dates_file(data, capsys):
    tmp, prices, events, holidays = data
    bad = tmp / "results.csv"
    bad.write_text("symbol,date\nTCS,4 Nov\n")
    args = ["--dir", str(tmp / "wf"), "init", "--from", "2026-11-02", "--to", "2026-11-06", "--cash", "1000000",
            "--prices", str(prices), "--events", str(events), "--holidays", str(holidays), "--results-dates", str(bad)]
    assert main(args) == 2 and "results.csv:2" in capsys.readouterr().err
    assert not (tmp / "wf").exists()


def test_the_report_names_the_day_the_kill_switch_latched(data, capsys):
    from datetime import date

    from swingbot.risk import KillSwitch

    tmp, prices, events, holidays = data
    run = Run(tmp / "wf")
    run.init(date(2026, 11, 2), date(2026, 11, 6), 1_000_000, prices, events, str(holidays))
    KillSwitch(tmp / "wf/state").latch("equity 940,000 is 6.00% under the week's open")
    assert run.next(lambda _: None) == "done"
    report = run.report()
    assert "kill switch latched on 2026-11-02** (equity 940,000 is 6.00% under the week's open)" in report
    assert "The 4 session(s) after it placed no entries" in report and "init --from 2026-11-03" in report
    assert main(["--dir", str(tmp / "wf"), "status"]) == 0
    assert "latched on 2026-11-02" in capsys.readouterr().out


def test_a_run_with_no_latch_says_nothing_about_it(data):
    from datetime import date

    tmp, prices, events, holidays = data
    run = Run(tmp / "wf")
    run.init(date(2026, 11, 2), date(2026, 11, 2), 1_000_000, prices, events, str(holidays))
    run.next(lambda _: None)
    assert run.latched() is None and "kill switch" not in run.report()


def test_select_events_drops_roundups_and_keeps_the_first_per_stock():
    from types import SimpleNamespace

    import pandas as pd

    from swingbot.daily.loop import select_events

    def ev(i, ticker, at, headline):
        return SimpleNamespace(event_id=i, ticker=ticker, published_at=pd.Timestamp(at, tz="Asia/Kolkata"),
                               headline=headline)

    events = [
        ev("b", "INDIGO", "2026-07-01 12:00", "IndiGo Lite fare, retold"),
        ev("a", "INDIGO", "2026-07-01 10:00", "IndiGo launches Lite fares"),
        ev("r1", "TCS", "2026-07-01 09:00", "Stocks to watch: TCS, Infosys"),
        ev("r2", "INFY", "2026-07-01 09:00", "Stocks to watch: TCS, Infosys"),
        ev("t", "TCS", "2026-07-01 11:00", "TCS wins a deal"),
    ]
    assert [e.event_id for e in select_events(events)] == ["r1", "r2", "a", "t", "b"]
    assert [e.event_id for e in select_events(events, skip_roundups=True)] == ["a", "t", "b"]
    assert [e.event_id for e in select_events(events, per_stock=1, skip_roundups=True)] == ["a", "t"]
    assert [e.event_id for e in select_events(events, per_stock=1)] == ["r1", "r2", "a"]


def test_a_run_with_selection_briefs_only_the_chosen_news(data):
    tmp, prices, events, holidays = data
    with events.open("a") as fh:
        fh.write("e1b,TCS,2026-11-02 19:00+05:30,TCS order retold,,test\n"
                 "r,TCS,2026-11-02 20:00+05:30,Stocks to watch: TCS and more,,test\n"
                 "r,INFY,2026-11-02 20:00+05:30,Stocks to watch: TCS and more,,test\n")
    from datetime import date
    run = Run(tmp / "wf")
    run.init(date(2026, 11, 2), date(2026, 11, 6), 1_000_000, prices, events, str(holidays),
             per_stock=1, skip_roundups=True)
    assert run.next(lambda *_: None) == "brief"
    brief = (tmp / "wf/days/2026-11-03/brief.md").read_text()
    assert "### e1" in brief and "e1b" not in brief and "Stocks to watch" not in brief
