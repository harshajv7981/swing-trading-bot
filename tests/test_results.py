import json
from datetime import date

import pandas as pd
import pytest

from swingbot.models import IST, NewsEvent
from swingbot.risk.cli import main as risk_main
from swingbot.risk.results import meeting_date, next_results_dates, notices

from test_risk import trend


def filing(ticker, at, body, headline="Board Meeting Intimation", eid=None, source="nse-filing"):
    return NewsEvent(eid or f"{ticker}-{at}", ticker, pd.Timestamp(at, tz=IST).to_pydatetime(),
                     f"{ticker} Ltd: {headline}", body, source)


@pytest.mark.parametrize("text, expected", [
    ("Reliance Industries Limited has informed the Exchange that Board meeting of the Company will be "
     "held on 14-Nov-2026, inter alia, to consider and approve the financial results", date(2026, 11, 14)),
    ("the meeting of the Board of Directors is scheduled on November 5, 2026 to approve unaudited results",
     date(2026, 11, 5)),
    ("intimation dated 20/10/2026: a meeting of the Board will be held on 03/11/2026 to consider results",
     date(2026, 11, 3)),
    ("Board meeting to be held on Thursday, 12th November, 2026 for quarterly results", date(2026, 11, 12)),
    ("no date in this one", None),
])
def test_meeting_date_reads_the_date_after_held_on(text, expected):
    assert meeting_date(text) == expected


def test_only_results_notices_count():
    events = [
        filing("ABC", "2026-10-20 18:00", "Board meeting will be held on 14-Nov-2026 to consider financial results"),
        filing("DEF", "2026-10-20 18:00", "Board meeting will be held on 14-Nov-2026 to consider a dividend"),
        filing("GHI", "2026-10-20 18:00", "results approved on 14-Nov-2026", headline="Outcome of Board Meeting"),
        filing("JKL", "2026-10-20 18:00", "Board meeting will be held on 14-Nov-2026 to consider results",
               source="gdelt:example.com"),
    ]
    assert [n.ticker for n in notices(events)] == ["ABC"]


def test_point_in_time_and_the_latest_notice_wins():
    events = [
        filing("ABC", "2026-10-20 18:00", "Board meeting will be held on 10-Nov-2026 to consider results", eid="a1"),
        filing("ABC", "2026-10-28 18:00", "Board meeting will now be held on 13-Nov-2026 to consider results",
               eid="a2"),
    ]
    # Before any notice: unknown, not "no results soon".
    assert next_results_dates(events, "2026-10-19 09:00") == {}
    # After the first notice, before the second was filed.
    assert next_results_dates(events, "2026-10-25 09:00") == {"ABC": date(2026, 11, 10)}
    # The rescheduling notice replaces it.
    assert next_results_dates(events, "2026-10-29 09:00") == {"ABC": date(2026, 11, 13)}
    # Once the meeting has passed there is no next date until a new notice.
    assert next_results_dates(events, "2026-11-14 09:00") == {}


def test_check_reads_results_dates_from_notices_and_blocks_in_the_blackout(tmp_path, capsys):
    prices = tmp_path / "prices"
    prices.mkdir()
    b = trend(start="2025-09-01")
    b.to_csv(prices / "RELIANCE.csv", index_label="date")
    last = b.index[-1]
    as_of = f"{last:%Y-%m-%d}"
    meeting = (last + pd.offsets.BDay(1)).date()  # the very next session: inside the 2-session blackout
    events = tmp_path / "events.csv"
    events.write_text(
        "event_id,ticker,published_at,headline,body,source\n"
        f"n1,RELIANCE,{(last - pd.Timedelta(days=10)):%Y-%m-%d} 18:00+05:30,"
        "Reliance Industries Ltd: Board Meeting Intimation,"
        f"\"Board meeting will be held on {meeting:%d-%b-%Y}, inter alia, to approve the financial results\","
        "nse-filing\n")
    data_cfg = tmp_path / "data.toml"
    data_cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n'
                        f'events_file = "{events}"\n')
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"equity": 500000, "cash": 500000, "week_open_equity": 500000,
                              "day_open_equity": 500000, "positions": []}))
    common = ["--data-config", str(data_cfg), "--state-dir", str(tmp_path / "state")]

    assert risk_main([*common, "results", "--as-of", as_of]) == 0
    assert f"RELIANCE     {meeting}" in capsys.readouterr().out
    assert risk_main([*common, "plan", "RELIANCE", "--as-of", as_of, "--out", str(tmp_path / "plan.json")]) == 0
    assert risk_main([*common, "size", str(tmp_path / "plan.json"), "--portfolio", str(pf),
                      "--out", str(tmp_path / "order.json")]) == 0
    capsys.readouterr()
    assert risk_main([*common, "check", str(tmp_path / "order.json"), "--portfolio", str(pf),
                      "--as-of", as_of]) == 1
    out = capsys.readouterr().out
    assert "BLOCK R9 results blackout" in out and str(meeting) in out


def test_hand_typed_dates_fill_gaps_but_stale_ones_are_dropped(tmp_path):
    from swingbot.data import DataConfig, Router
    from swingbot.risk.results import from_router

    manual = tmp_path / "results_dates.csv"
    manual.write_text("symbol,date\nTCS,2026-11-09\nINFY,2026-10-01\n")
    router = Router(DataConfig(sources={"prices": "csv"}, events_file=tmp_path / "missing.csv"))
    assert from_router(router, pd.Timestamp("2026-10-27 08:00", tz=IST), manual) == {"TCS": date(2026, 11, 9)}


def test_a_symbol_can_have_a_whole_season_of_typed_dates(tmp_path):
    from swingbot.data import DataConfig, Router
    from swingbot.risk.results import from_router

    manual = tmp_path / "results_dates.csv"
    manual.write_text("symbol,date\nTCS,2026-10-09\ntcs,2027-01-12\nINFY,2026-10-16\nINFY,2026-11-20\n")
    router = Router(DataConfig(sources={"prices": "csv"}, events_file=tmp_path / "missing.csv"))
    as_of = pd.Timestamp("2026-10-27 08:00", tz=IST)
    assert from_router(router, as_of, manual) == {"INFY": date(2026, 11, 20), "TCS": date(2027, 1, 12)}
    assert from_router(router, pd.Timestamp("2026-10-01 08:00", tz=IST), manual) == {
        "INFY": date(2026, 10, 16), "TCS": date(2026, 10, 9)}


def test_the_sooner_of_a_notice_and_a_typed_date_wins(tmp_path):
    from swingbot.data import DataConfig, Router
    from swingbot.risk.results import from_router

    (tmp_path / "events.csv").write_text(
        "event_id,ticker,published_at,headline,body,source\n"
        "n1,ABC,2026-10-20 18:00+05:30,ABC Ltd: Board Meeting Intimation,"
        "Board meeting will be held on 05-Nov-2026 to consider financial results,nse-filing\n"
        "n2,DEF,2026-10-20 18:00+05:30,DEF Ltd: Board Meeting Intimation,"
        "Board meeting will be held on 12-Nov-2026 to consider financial results,nse-filing\n")
    manual = tmp_path / "results_dates.csv"
    manual.write_text("symbol,date\nABC,2026-11-14\nDEF,2026-11-10\n")
    router = Router(DataConfig(sources={"prices": "csv"}, events_file=tmp_path / "events.csv"))
    dates = from_router(router, pd.Timestamp("2026-10-27 08:00", tz=IST), manual)
    assert dates == {"ABC": date(2026, 11, 5), "DEF": date(2026, 11, 10)}


def test_a_bad_typed_line_says_where_it_is(tmp_path):
    from swingbot.risk.results import load_manual

    manual = tmp_path / "results_dates.csv"
    manual.write_text("symbol,date\nTCS,2026-11-09\nINFY,14-Nov-2026\n")
    with pytest.raises(ValueError, match=r"results_dates.csv:3"):
        load_manual(manual)
