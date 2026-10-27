from datetime import date

import pandas as pd
import pytest

from swingbot.data import DataConfig, Router, VendorUnavailable
from swingbot.data import refresh
from swingbot.data.refresh import RefreshSettings, last_closed_session
from swingbot.models import IST
from swingbot.prices import CSVPriceSource
from swingbot.risk.calendar import NSECalendar

CAL = NSECalendar({2026: {date(2026, 10, 20)}})  # Dussehra, a Tuesday


def at(s):
    return pd.Timestamp(s, tz=IST)


class FakeYahoo:
    """Every weekday's bar up to `through`; records each request."""

    def __init__(self, tickers=("AAA", "BBB"), through="2026-10-27"):
        days = pd.bdate_range("2025-06-02", through)
        self.series = {t: pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                                        "close": [100.0 + i for i in range(len(days))], "volume": 1e5},
                                       index=pd.DatetimeIndex(days, name="date"))
                       for t in tickers}
        self.calls, self.down = [], set()

    def __call__(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        if ticker in self.down:
            raise VendorUnavailable("Yahoo request failed: timeout")
        bars = self.series[ticker]
        return bars[(bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))]


@pytest.fixture
def env(tmp_path):
    universe = tmp_path / "universe.csv"
    universe.write_text("symbol,name,aliases\nAAA,Alpha Industries,Alpha\nBBB,Beta Bank,\n")
    cfg = DataConfig(sources={"prices": "csv,yfinance", "news": "events", "filings": "events",
                              "fundamentals": "nse_results"},
                     prices_dir=tmp_path / "prices", events_file=tmp_path / "news" / "events.csv",
                     cache_prices=False)
    router = Router(cfg)
    yahoo = FakeYahoo()
    router.downloaders = {"prices": {"yfinance": yahoo}}
    settings = RefreshSettings(universe=universe, state_file=tmp_path / "state.json", gdelt_pause=0, nse_pause=0)
    return router, settings, yahoo, tmp_path


def saved(env, t):
    return CSVPriceSource(env[3] / "prices").daily(t)


def go(env, now, **kw):
    router, settings, *_ = env
    kw.setdefault("only", ("prices",))
    kw.setdefault("tickers", ["AAA", "BBB"])
    return refresh.run(router, settings, now=at(now), calendar=CAL, sleep=lambda s: None, **kw)


def test_last_closed_session_skips_weekends_holidays_and_the_open_session():
    assert last_closed_session(at("2026-10-27 15:45"), CAL) == date(2026, 10, 27)
    assert last_closed_session(at("2026-10-27 11:00"), CAL) == date(2026, 10, 26)
    assert last_closed_session(at("2026-10-26 09:00"), CAL) == date(2026, 10, 23)  # Monday morning -> Friday
    assert last_closed_session(at("2026-10-21 10:00"), CAL) == date(2026, 10, 19)  # day after a holiday


def test_first_run_downloads_history_but_not_the_session_still_open(env):
    report = go(env, "2026-10-27 11:00")
    assert report.ok and {i.status for i in report.items} == {"updated"}
    bars = saved(env, "AAA")
    assert bars.index[-1] == pd.Timestamp("2026-10-26")  # today's bar hasn't closed
    assert len(bars) > 250


def test_second_run_is_a_no_op_and_the_next_day_adds_one_bar(env):
    go(env, "2026-10-26 16:00")
    yahoo = env[2]
    yahoo.calls.clear()
    again = go(env, "2026-10-26 18:00")
    assert {i.status for i in again.items} == {"current"} and yahoo.calls == []

    before = saved(env, "AAA")
    nxt = go(env, "2026-10-27 16:00")
    after = saved(env, "AAA")
    assert [i.added for i in nxt.items] == [1, 1]
    assert len(after) == len(before) + 1 and after.index.is_unique
    start = yahoo.calls[0][1]
    assert start == date(2026, 10, 19)  # only a week of overlap, not the whole history


def test_changed_adjustment_downloads_the_whole_file_again(env):
    go(env, "2026-10-23 16:00")
    yahoo = env[2]
    bars = yahoo.series["AAA"]
    yahoo.series["AAA"] = bars.assign(close=bars["close"] * 0.5)  # a 1:1 bonus, back-adjusted
    report = go(env, "2026-10-27 16:00", tickers=["AAA"])
    item = report.items[0]
    assert item.status == "rebuilt" and item.added == 2
    after = saved(env, "AAA")
    first = after.index[0]
    assert after.loc[first, "close"] == pytest.approx(yahoo.series["AAA"].loc[first, "close"])  # old rows replaced too


def test_one_failure_doesnt_stop_the_rest_and_is_retried_next_time(env):
    go(env, "2026-10-23 16:00")
    env[2].down.add("BBB")
    report = go(env, "2026-10-27 16:00")
    assert not report.ok and [i.ticker for i in report.failed()] == ["BBB"]
    assert saved(env, "AAA").index[-1] == pd.Timestamp("2026-10-27")
    env[2].down.clear()
    assert go(env, "2026-10-27 17:00").items[1].status == "updated"


def test_default_tickers_include_the_benchmark_and_mood_series(env):
    from swingbot import news
    names = refresh.price_tickers(news.load_universe(env[1].universe))
    assert names[:2] == ["AAA", "BBB"]
    assert {"NIFTY50", "NIFTYFMCG", "GOLDBEES", "USDINR", "INDIAVIX"} <= set(names)


def test_router_download_needs_a_downloading_source(env):
    router = env[0]
    router.config = router.config.with_sources(prices="csv")
    with pytest.raises(Exception, match="can download"):
        router.download("prices", "AAA", date(2026, 10, 1), date(2026, 10, 2))


# --- news and filings ---------------------------------------------------------------

def gdelt(articles_by_query, calls, fail=()):
    def fetch(query, start, end):
        calls.append((query, start, end))
        if any(f in query for f in fail):
            raise ValueError("Please limit requests to one every 5 seconds")
        return articles_by_query.get(query.split('"')[1], [])
    return fetch


ARTICLES = {"Alpha Industries": [
    {"title": "Alpha Industries wins order", "url": "https://x.in/1", "seendate": "20261026T050000Z", "domain": "x.in"},
    {"title": "Markets close flat", "url": "https://x.in/2", "seendate": "20261026T060000Z", "domain": "x.in"},
]}


def test_news_is_incremental_and_idempotent(env):
    calls = []
    first = go(env, "2026-10-27 16:00", only=("news",), gdelt_fetch=gdelt(ARTICLES, calls))
    by = {i.ticker: i for i in first.items}
    assert by["AAA"].added == 1 and by["BBB"].status == "no_new"
    assert calls[0][1] == date(2026, 10, 20)  # first run: a week back

    calls.clear()
    second = go(env, "2026-10-28 16:00", only=("news",), gdelt_fetch=gdelt(ARTICLES, calls))
    assert all(i.added == 0 for i in second.items)
    assert calls[0][1:] == (date(2026, 10, 26), date(2026, 10, 28))  # one day of overlap
    events = pd.read_csv(env[3] / "news" / "events.csv")
    assert list(events["headline"]) == ["Alpha Industries wins order"]


def test_failed_news_fetch_keeps_its_window_for_next_time(env):
    calls = []
    report = go(env, "2026-10-27 16:00", only=("news",), gdelt_fetch=gdelt(ARTICLES, calls, fail=["Beta"]))
    assert [i.ticker for i in report.failed()] == ["BBB"]
    calls.clear()
    go(env, "2026-10-30 16:00", only=("news",), gdelt_fetch=gdelt(ARTICLES, calls))
    starts = {}
    for q, s, _ in calls:
        starts.setdefault(q.split('"')[1], s)
    assert starts == {"Alpha Industries": date(2026, 10, 26), "Beta Bank": date(2026, 10, 23)}


class FakeNSE:
    def announcements(self, symbol, start, end):
        if symbol != "AAA":
            return []
        return [{"symbol": "AAA", "desc": "Outcome of Board Meeting", "sm_name": "Alpha Industries Ltd",
                 "exchdisstime": "26-Oct-2026 17:05:00", "seq_id": "1", "attchmntText": "Dividend declared"},
                {"symbol": "AAA", "desc": "Trading Window", "exchdisstime": "26-Oct-2026 18:00:00", "seq_id": "2"}]


def test_filings_need_the_terms_flag(env):
    skipped = go(env, "2026-10-27 16:00", only=("filings",), nse_session=FakeNSE)
    assert [(i.status, i.ticker) for i in skipped.items] == [("skipped", "*")]
    assert not (env[3] / "news" / "events.csv").exists()

    took = go(env, "2026-10-27 16:00", only=("filings",), nse_session=FakeNSE, accept_nse_terms=True)
    assert took.ok and {i.ticker: i.added for i in took.items} == {"AAA": 1, "BBB": 0}  # routine filing dropped
    rows = pd.read_csv(env[3] / "news" / "events.csv")
    assert rows["source"].tolist() == ["nse-filing"]
    assert "Dividend declared" in env[0].text("filings", "AAA", at("2026-10-27 09:00"))  # the router serves it


def test_nse_session_failure_marks_every_stock_failed(env):
    def broken():
        raise OSError("403 Forbidden")
    report = go(env, "2026-10-27 16:00", only=("filings",), nse_session=broken, accept_nse_terms=True)
    assert [i.status for i in report.items] == ["failed", "failed"]


def test_a_second_run_cannot_overlap(env):
    with refresh._lock(env[1].state_file.with_name("refresh.lock")):
        with pytest.raises(RuntimeError, match="another refresh"):
            go(env, "2026-10-27 16:00")
