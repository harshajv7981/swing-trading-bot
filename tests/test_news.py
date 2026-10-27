import csv
from datetime import date

import pytest

from swingbot.events import load_events
from swingbot.news import (
    Company,
    dedupe,
    gdelt_query,
    gdelt_rows,
    is_routine,
    merge_into,
    nse_rows,
)

RIL = Company("RELIANCE", "Reliance Industries", ("RIL",))
LT = Company("LT", "Larsen & Toubro", ("L&T", "Larsen"))


def test_gdelt_query_skips_ampersand_names():
    # "RIL" is too short for GDELT (it rejects the whole query), but still matches headlines.
    assert gdelt_query(RIL) == '"Reliance Industries" sourcecountry:IN sourcelang:english'
    assert gdelt_query(LT) == '"Larsen" sourcecountry:IN sourcelang:english'


def test_gdelt_rows_need_company_in_headline_and_convert_to_ist():
    articles = [
        {"url": "https://a.in/1", "title": "RIL shares jump on Jio listing plan", "seendate": "20260803T023000Z",
         "domain": "a.in"},
        {"url": "https://a.in/2", "title": "Sensex ends flat; banks drag", "seendate": "20260803T100000Z"},
        {"url": "https://a.in/3", "title": "PRILL prices fall", "seendate": "20260803T100000Z"},  # not a word match
    ]
    rows = gdelt_rows(articles, RIL)
    assert [r["headline"] for r in rows] == ["RIL shares jump on Jio listing plan"]
    assert rows[0]["published_at"] == "2026-08-03T08:00:00+05:30"  # 02:30 UTC, before the 09:15 open
    assert rows[0]["source"] == "gdelt:a.in" and rows[0]["ticker"] == "RELIANCE"


def test_ampersand_alias_matches_headline():
    assert LT.mentioned_in("L&T bags mega order from NHAI")


def test_nse_rows_use_dissemination_time_and_skip_paperwork():
    raw = [
        {"symbol": "RELIANCE", "sm_name": "Reliance Industries Limited", "desc": "Outcome of Board Meeting",
         "attchmntText": "  Board approved   Q1 results. ", "an_dt": "18-Jul-2026 19:01:00",
         "exchdisstime": "18-Jul-2026 19:01:42", "seq_id": "1", "attchmntFile": "https://nse/x.pdf"},
        {"symbol": "RELIANCE", "desc": "Trading Window-XBRL", "an_dt": "20-Jul-2026 10:00:00", "seq_id": "2"},
        {"symbol": "RELIANCE", "desc": "Acquisition", "an_dt": "21-Jul-2026 08:30:00", "seq_id": "3"},
    ]
    rows = nse_rows(raw)
    assert [r["headline"] for r in rows] == ["Reliance Industries Limited: Outcome of Board Meeting", "RELIANCE: Acquisition"]
    assert rows[0]["published_at"] == "2026-07-18T19:01:42+05:30"
    assert rows[0]["body"] == "Board approved Q1 results."
    assert rows[1]["published_at"] == "2026-07-21T08:30:00+05:30"
    assert is_routine("Copy of Newspaper Publication")


def test_dedupe_keeps_earliest_syndicated_copy():
    a = {"event_id": "a", "ticker": "X", "published_at": "2026-08-03T10:00:00+05:30", "headline": "X wins order!"}
    b = {"event_id": "b", "ticker": "X", "published_at": "2026-08-03T09:00:00+05:30", "headline": "X wins order"}
    assert [r["event_id"] for r in dedupe([a, b, dict(a)])] == ["b"]


def test_merge_writes_a_file_the_backtest_loads(tmp_path):
    out = tmp_path / "events.csv"
    rows = gdelt_rows([{"url": "https://a.in/1", "title": "Reliance Industries Q1 profit rises",
                        "seendate": "20260718T140000Z"}], RIL)
    assert merge_into(out, rows) == (1, 1)
    assert merge_into(out, rows) == (0, 1)  # re-running a day adds nothing
    with open(out) as f:
        assert "url" in next(csv.reader(f))
    (ev,) = load_events(out)
    assert ev.ticker == "RELIANCE" and ev.published_at.utcoffset().total_seconds() == 19800


def test_gdelt_retries_rate_limits_but_not_a_bad_query(monkeypatch):
    import io
    import urllib.error

    from swingbot.data import remote

    waits, calls = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(answers):
        def urlopen(req, timeout=60):
            calls.append(req.full_url)
            a = answers.pop(0)
            if isinstance(a, int):
                raise urllib.error.HTTPError(req.full_url, a, "Too Many Requests", {}, None)
            return Resp(a.encode())
        return urlopen

    monkeypatch.setattr(remote.time, "sleep", waits.append)
    monkeypatch.setattr(remote.urllib.request, "urlopen", opener([429, 429, '{"articles": [{"url": "u"}]}']))
    assert remote.gdelt_articles("q", date(2026, 7, 1), date(2026, 7, 7)) == [{"url": "u"}]
    assert waits == [30.0, 60.0]

    calls.clear()
    monkeypatch.setattr(remote.urllib.request, "urlopen", opener(["The specified phrase is too short."]))
    with pytest.raises(ValueError, match="too short"):
        remote.gdelt_articles("q", date(2026, 7, 1), date(2026, 7, 7))
    assert len(calls) == 1
