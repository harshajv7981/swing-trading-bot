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
