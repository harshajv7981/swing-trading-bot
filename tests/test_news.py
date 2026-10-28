import csv
from datetime import date

import pandas as pd
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


def gkg_line(date, source, url, title, orgs=""):
    cols = [""] * 27
    cols[0], cols[1], cols[3], cols[4], cols[14] = "1", date, source, url, orgs
    cols[26] = f"<PAGE_PRECISEPUBTIMESTAMP>x</PAGE_PRECISEPUBTIMESTAMP><PAGE_TITLE>{title}</PAGE_TITLE>"
    return "\t".join(cols) + "\n"


def test_gkg_rows_keep_indian_titles_naming_a_company():
    from swingbot.news import HeadlineMatcher, gkg_rows

    tatasteel = Company("TATASTEEL", "Tata Steel")
    tatamotors = Company("TATAMOTORS", "Tata Motors")
    m = HeadlineMatcher([RIL, LT, tatasteel, tatamotors])
    lines = [
        gkg_line("20260803023000", "economictimes.indiatimes.com", "https://et.com/1",
                 "RIL and Tata Steel shares rise"),
        gkg_line("20260803023000", "livemint.com", "https://mint.com/2", "L&amp;T wins Rs 5,000 crore order"),
        gkg_line("20260803023000", "reuters.com", "https://reuters.com/3", "Reliance Industries profit beats"),
        gkg_line("20260803023000", "thehindu.com", "https://hindu.com/4", "Sensex ends flat"),
        gkg_line("20260803023000", "moneycontrol.com", "https://mc.com/5", "PRILL prices fall"),
        "short\tline\n",
    ]
    rows = gkg_rows(lines, m)
    assert sorted((r["ticker"], r["headline"]) for r in rows) == [
        ("LT", "L&T wins Rs 5,000 crore order"),       # HTML entity undone, & alias matched
        ("RELIANCE", "RIL and Tata Steel shares rise"),
        ("TATASTEEL", "RIL and Tata Steel shares rise"),  # one story, two companies
    ]
    assert rows[0]["published_at"] == "2026-08-03T08:00:00+05:30"
    assert rows[0]["source"] == "gdelt:economictimes.indiatimes.com"
    # Reuters is kept only when asked for any outlet.
    assert {r["url"] for r in gkg_rows(lines, m, any_source=True)} >= {"https://reuters.com/3"}


def test_gkg_rows_share_ids_with_the_doc_api():
    from swingbot.news import HeadlineMatcher, gkg_rows

    line = gkg_line("20260803023000", "livemint.com", "https://a.in/1", "RIL shares jump")
    bulk = gkg_rows([line], HeadlineMatcher([RIL]))
    api = gdelt_rows([{"url": "https://a.in/1", "title": "RIL shares jump", "seendate": "20260803T023000Z"}], RIL)
    assert bulk[0]["event_id"] == api[0]["event_id"]


def test_indian_source():
    from swingbot.news import indian_source

    assert indian_source("economictimes.indiatimes.com")
    assert indian_source("businesstoday.in") and indian_source("livemint.com")
    assert not indian_source("reuters.com") and not indian_source("notlivemint.com")


def test_gkg_lines_reads_a_zip_and_skips_missing_batches(monkeypatch):
    import io
    import urllib.error
    import zipfile
    from datetime import UTC, datetime

    from swingbot.data import remote

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("20260803023000.gkg.csv", "a\tb\nc\td\n")
    payload = buf.getvalue()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        if "20260803024500" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        assert req.full_url == "http://data.gdeltproject.org/gdeltv2/20260803023000.gkg.csv.zip"
        return Resp(payload)

    monkeypatch.setattr(remote.urllib.request, "urlopen", fake)
    lines, size = remote.gkg_lines(datetime(2026, 8, 3, 2, 30, tzinfo=UTC))
    assert lines == ["a\tb", "c\td"] and size == len(payload)
    assert remote.gkg_lines(datetime(2026, 8, 3, 2, 45, tzinfo=UTC)) is None


def test_matcher_skips_other_companies_and_non_news():
    from swingbot.news import HeadlineMatcher

    sbi = Company("SBIN", "State Bank of India", ("SBI",))
    vedl = Company("VEDL", "Vedanta")
    kotak = Company("KOTAKBANK", "Kotak Mahindra Bank")
    m = HeadlineMatcher([LT, sbi, vedl, kotak])
    assert m.companies("L&T Technology Services launches AI lab") == []
    assert m.companies("Hold Larsen and Toubro Technology Services, says ICICI") == []
    assert m.companies("Vedanta Iron & Steel lists at a premium") == []
    assert m.companies("SBI Dividend Yield Fund: NAV today") == []
    assert m.companies("SBI Apprentice Admit Card 2026 out") == []
    assert m.companies("Legal Manager, Kotak Mahindra Bank, Mumbai - Job") == []
    assert m.companies("Legal Manager, Kotak Mahindra Bank Limited [Mumbai], Job") == []
    assert m.companies("Vedanta Iron shares rally on debut") == []
    assert [c.symbol for c in m.companies("Kotak Mahindra Bank profit rises")] == ["KOTAKBANK"]
    assert m.companies("SBI Research sees rate cut") == []
    # The parent still matches, also next to a spun-off name.
    # The parent still counts when the title names it on its own too.
    assert [c.symbol for c in m.companies("Vedanta shares fall after Vedanta Power lists")] == ["VEDL"]
    assert [c.symbol for c in m.companies("L&T wins order; L&T Finance up")] == ["LT"]
    assert [c.symbol for c in m.companies("SBI sells 1.42% stake in SBI Funds Management")] == ["SBIN"]
    assert m.companies("SBI Card shares slip 3%") == []
    assert m.companies("SBI Capital flags December hike risk") == []
    assert m.companies("SBI Funds IPO subscribed 2.77x on Day 2") == []
    assert [c.symbol for c in m.companies("SBI raises lending rates")] == ["SBIN"]
    assert [c.symbol for c in m.companies("SBI Recruitment 2026: apply for 500 posts")] == []
    assert [c.symbol for c in m.companies("SBI job cuts hit branch staff")] == ["SBIN"]  # news about jobs stays


def test_gkg_rows_drop_recrawled_old_pages():
    from swingbot.news import HeadlineMatcher, gkg_rows, stale_url

    seen = pd.Timestamp("2026-07-01 12:00", tz="Asia/Kolkata")
    assert stale_url("https://a.in/2013/05/07/ril-story", seen)
    assert stale_url("https://a.in/news-20250101-ril", seen)
    assert not stale_url("https://a.in/2026/06/30/ril-story", seen)
    assert not stale_url("https://a.in/articleshow/120260512345.cms", seen)  # long ids are not dates
    lines = [gkg_line("20260701063000", "livemint.com", "https://livemint.com/2013/05/07/x", "RIL shares jump"),
             gkg_line("20260701063000", "livemint.com", "https://livemint.com/2026/07/01/y", "RIL shares jump")]
    assert [r["url"] for r in gkg_rows(lines, HeadlineMatcher([RIL]))] == ["https://livemint.com/2026/07/01/y"]


def test_refilter_drops_rows_todays_rules_reject():
    from swingbot.news import HeadlineMatcher

    lodha = Company("LODHA", "Lodha Developers", ("Lodha",))
    m = HeadlineMatcher([lodha, RIL])
    rows = [
        {"ticker": "LODHA", "headline": "Lodha Developers sales rise 10%", "source": "gdelt:mint.com"},
        {"ticker": "LODHA", "headline": "Amitabh Bachchan buys land: Abhinandan Lodha", "source": "gdelt:bt.in"},
        {"ticker": "LODHA", "headline": "Shailesh Lodha's daughter gets married", "source": "gdelt:abp.in"},
        {"ticker": "RELIANCE", "headline": "Roundup: Lodha, RIL in focus", "source": "gdelt:et.com"},
        {"ticker": "LODHA", "headline": "Lodha: Board meeting outcome", "source": "nse-filing"},
    ]
    kept, dropped = m.refilter(rows)
    assert [r["headline"] for r in dropped] == [
        "Amitabh Bachchan buys land: Abhinandan Lodha", "Shailesh Lodha's daughter gets married"]
    assert len(kept) == 3


def test_matcher_case_rules_drop_common_words_and_people():
    from swingbot.news import HeadlineMatcher, case_ok

    assert case_ok("HAL", "HAL") and not case_ok("Hal", "HAL")
    assert case_ok("Titan", "Titan") and not case_ok("titan", "Titan")
    titan = Company("TITAN", "Titan Company", ("Titan",))
    gail = Company("GAIL", "GAIL India", ("GAIL",))
    m = HeadlineMatcher([titan, gail])
    assert m.companies("Nilekani, the tech titan") == []
    assert m.companies("Attack on Titan gets a sequel") == []
    assert m.companies("Gail Porter opens a restaurant") == []
    assert [c.symbol for c in m.companies("RCF, Gail enter into MoU for urea plant")] == ["GAIL"]
    assert [c.symbol for c in m.companies("Titan Q1 sales up 20%")] == ["TITAN"]
    assert [c.symbol for c in m.companies("GAIL SHARES JUMP 5%")] == ["GAIL"]


def test_matcher_drops_rivers_exams_taxes_and_etfs():
    from swingbot.news import HeadlineMatcher, load_universe

    m = HeadlineMatcher(load_universe("data/universe/nifty100.csv"))
    for title in ["Power grid bottlenecks slow solar", "Floods on the River Trent", "RRB NTPC result out",
                  "SBI IT ETF NAV today", "GST and ITC fraud: raids in Surat", "TCS credit rules eased",
                  "Coal India Open golf", "DLF-3 demolition drive", "Motherson Sumi Wiring Q1"]:
        assert m.companies(title) == [], title
    for title, sym in [("Power Grid Q1 profit", "POWERGRID"), ("Trent Q1 revenue up", "TRENT"),
                       ("NTPC commissions plant", "NTPC"), ("TCS Q2 results", "TCS"), ("DLF sales", "DLF")]:
        assert [c.symbol for c in m.companies(title)] == [sym], title
