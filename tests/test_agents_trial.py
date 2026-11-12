import json
from datetime import date

import pandas as pd
import pytest

from swingbot import agents_trial as at


def write(folder, sym, closes, start="2026-11-02"):
    days = pd.bdate_range(start, periods=len(closes))
    pd.DataFrame({"Date": days.strftime("%Y-%m-%d"), "Close": closes, "Series": "EQ"}).to_csv(
        folder / f"{sym.lower()}.csv", index=False)


def test_last_friday():
    assert at.last_friday(date(2026, 11, 6)) == date(2026, 11, 6)   # Friday itself
    assert at.last_friday(date(2026, 11, 8)) == date(2026, 11, 6)   # Sunday
    assert at.last_friday(date(2026, 11, 5)) == date(2026, 10, 30)  # Thursday


def test_call_cost_uses_the_long_prompt_price_above_the_cut():
    assert at.call_cost("claude-haiku-5-5", 1_000_000 // 20, 10_000) == pytest.approx(0.005 + 0.005)
    assert at.call_cost("claude-haiku-5-5", 200_000, 0) == pytest.approx(0.10)


def test_window_enters_at_the_first_close_after_the_date():
    s = pd.Series([100, 110, 111, 112, 113, 114, 121.0], index=pd.bdate_range("2026-11-06", periods=7))
    entry, exit_, r = at.window(s, date(2026, 11, 6))
    assert entry == pd.Timestamp("2026-11-09") and exit_ == pd.Timestamp("2026-11-16")
    assert r == pytest.approx(121 / 110 - 1)
    assert at.window(s.iloc[:6], date(2026, 11, 6)) is None  # exit close not in the data yet


def test_score_books_long_calls_against_equal_weight(tmp_path):
    # Two stocks; decision Friday 6 Nov. A rises 10% over the hold, B falls 10%.
    write(tmp_path, "A", [100, 100, 101, 102, 103, 104, 110.0], start="2026-11-06")
    write(tmp_path, "B", [100, 100, 99, 98, 97, 96, 90.0], start="2026-11-06")
    write(tmp_path, at.INDEX, [100] * 7, start="2026-11-06")
    decisions = [{"date": "2026-11-06", "symbol": "A", "rating": "Buy"},
                 {"date": "2026-11-06", "symbol": "B", "rating": "Sell"}]
    res = at.score(decisions, tmp_path, stocks=["A", "B"])
    (w,) = res["weeks"]
    assert w["longs"] == ["A"]
    assert w["gross"] == pytest.approx(0.10)
    assert w["net"] == pytest.approx(0.10 - 0.002)  # bought 100% of the book
    assert w["ew"] == pytest.approx(0.0)
    assert w["index"] == pytest.approx(0.0)
    text = at.report(res, decisions)
    assert "| Buy | 1 | +10.00% | 100% |" in text


def test_score_skips_weeks_without_closes(tmp_path):
    write(tmp_path, "A", [100, 101.0], start="2026-11-06")
    res = at.score([{"date": "2026-11-06", "symbol": "A", "rating": "Buy"}], tmp_path, stocks=["A"])
    assert res["weeks"] == []


def test_stocks_are_the_preregistered_sample():
    import csv
    import random
    from pathlib import Path

    rows = csv.DictReader(open(Path(__file__).parents[1] / "data/universe/midcap150.csv"))
    sample = random.Random(20261102).sample(sorted(r["symbol"] for r in rows), 20)
    assert sorted(sample) == at.STOCKS


def test_record_validates_and_refuses_duplicates(tmp_path):
    log = tmp_path / "d.jsonl"
    today = date(2026, 11, 8)
    row = at.record(date(2026, 11, 6), "AUBANK", "overweight", "steady deposits", model="m", path=log,
                    today=today)
    assert row["rating"] == "Overweight" and row["engine"] == "skill"
    with pytest.raises(ValueError, match="already"):
        at.record(date(2026, 11, 6), "AUBANK", "Buy", path=log, today=today)
    with pytest.raises(ValueError, match="one of"):
        at.record(date(2026, 11, 6), "SRF", "Strong buy", path=log, today=today)
    with pytest.raises(ValueError, match="20 stocks"):
        at.record(date(2026, 11, 6), "TCS", "Buy", path=log, today=today)
    with pytest.raises(ValueError, match="days ago"):
        at.record(date(2026, 11, 6), "SRF", "Buy", path=log, today=date(2026, 11, 10))
    with pytest.raises(ValueError, match="future"):
        at.record(date(2026, 11, 9), "SRF", "Buy", path=log, today=today)
    assert at.pending(date(2026, 11, 6), ["AUBANK", "SRF"], at.read_decisions(log)) == ["SRF"]


def test_too_soon_keeps_hold_windows_apart():
    ds = [{"date": "2026-11-06", "symbol": "SRF", "rating": "Hold"}]
    today = date(2026, 12, 1)
    assert at.too_soon(date(2026, 11, 6), ds, today) is None          # same week, finishing it
    assert "2026-11-13" in at.too_soon(date(2026, 11, 10), ds, today)
    assert at.too_soon(date(2026, 11, 13), ds, today) is None
    assert "future" in at.too_soon(date(2026, 12, 5), ds, today)


def test_past_calls_show_outcomes(tmp_path):
    write(tmp_path, "SRF", [100, 100, 101, 102, 103, 104, 110.0], start="2026-11-06")
    ds = [{"date": "2026-11-06", "symbol": "SRF", "rating": "Buy"}]
    text = at.past_calls("SRF", date(2026, 11, 13), ds, tmp_path)
    assert "2026-11-06: Buy (5-day return +10.0%)" in text
    assert at.past_calls("SRF", date(2026, 11, 6), ds, tmp_path) == "No earlier calls on this stock."


def test_cli_record_refusal_exits_2(capsys):
    assert at.main(["record", "--date", "2020-01-03", "--symbol", "SRF", "--rating", "Buy"]) == 2
    assert "not recorded" in capsys.readouterr().err


def test_company_news_falls_back_when_yahoo_has_nothing(monkeypatch):
    class FakeRouter:
        def text(self, category, symbol, as_of, days):
            return f"{category} for {symbol}: headline"

    import swingbot.data.router as r
    monkeypatch.setattr(r, "Router", FakeRouter)
    ok = at.company_news("SRF.NS", "SRF", "2026-10-30", "2026-11-06", lambda *a: "Yahoo: big order win")
    assert ok == "Yahoo: big order win"
    gone = at.company_news("SRF.NS", "SRF", "2026-10-30", "2026-11-06",
                           lambda *a: "DATA_UNAVAILABLE: no configured vendor")
    assert "DATA_UNAVAILABLE" in gone and "news for SRF: headline" in gone


def test_ampersand_ticker_gets_a_safe_cache_name():
    pytest.importorskip("tradingagents")
    from tradingagents.dataflows.vendors.yahoo import ohlcv

    at._allow_ampersand_tickers()
    assert ohlcv.safe_ticker_component("GVT&D.NS") == "GVT_AND_D.NS"
    with pytest.raises(ValueError):
        ohlcv.safe_ticker_component("../etc")


def test_ampersand_ticker_is_safe_in_the_graph_run_log():
    pytest.importorskip("tradingagents")
    from tradingagents.graph import checkpointer, trading_graph

    at._allow_ampersand_tickers(graph=True)
    at._allow_ampersand_tickers(graph=True)   # a second run in the same process wraps nothing twice
    assert trading_graph.safe_ticker_component("GVT&D.NS") == "GVT_AND_D.NS"
    assert checkpointer.safe_ticker_component("GVT&D.NS") == "GVT_AND_D.NS"
    with pytest.raises(ValueError):
        trading_graph.safe_ticker_component("../etc")


def test_run_refusal_follows_the_skills_forward_only_rules():
    ds = [{"date": "2026-11-06", "symbol": "SRF", "rating": "Hold"}]
    assert at.run_refusal(date(2026, 11, 13), ds, today=date(2026, 11, 13)) is None
    assert at.run_refusal(date(2026, 11, 13), ds, today=date(2026, 11, 15)) is None     # over the weekend
    assert "days ago" in at.run_refusal(date(2026, 11, 13), ds, today=date(2026, 11, 18))
    assert "2026-11-13" in at.run_refusal(date(2026, 11, 12), ds, today=date(2026, 11, 12))
    assert "future" in at.run_refusal(date(2026, 11, 20), ds, today=date(2026, 11, 13))


def test_repo_news_vendor_cuts_the_window_at_the_decision_date(monkeypatch):
    asked = {}

    class FakeRouter:
        def text(self, category, symbol, as_of, days):
            asked.update(category=category, symbol=symbol, as_of=as_of, days=days)
            return "headline"

    import swingbot.data.router as r
    monkeypatch.setattr(r, "Router", FakeRouter)
    get_news = at.repo_news_vendor(date(2026, 11, 13))
    text = get_news("GVT&D.NS", "2026-11-06", "2026-11-20")      # the model asked past the decision date
    assert "headline" in text and "2026-11-13" in text
    assert asked["symbol"] == "GVT&D" and asked["days"] == 7
    assert asked["as_of"].date() == date(2026, 11, 13)


def _fake_graph(monkeypatch, usd_per_stock=0.0, fail=()):
    """Stand in for TradingAgentsGraph: no model calls, a rating per stock, a spend on the meter."""
    pytest.importorskip("tradingagents")
    from tradingagents.graph import trading_graph

    class FakeGraph:
        def __init__(self, selected_analysts, config, callbacks):
            self.config, self.meter = config, callbacks[0]

        def propagate(self, ticker, day):
            self.meter.calls += 1
            self.meter.usd += usd_per_stock
            if ticker in fail:
                raise RuntimeError("boom")
            return {}, "Overweight"

    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", FakeGraph)
    return FakeGraph


def test_run_logs_api_decisions_and_skips_what_the_skill_decided(tmp_path, monkeypatch):
    _fake_graph(monkeypatch, fail=("SRF.NS",))
    monkeypatch.setenv(at.KEY_ENV, "test-key")
    monkeypatch.chdir(tmp_path)
    log, day = tmp_path / "d.jsonl", date(2026, 11, 13)
    at.record(day, "AUBANK", "Hold", path=log, today=day)
    lines = []
    errors = at.run(day, ["AUBANK", "GVT&D", "SRF"], path=log, log=lines.append, today=day)
    rows = {d["symbol"]: d for d in at.read_decisions(log)}
    assert errors == 1 and "already decided" in lines[0]
    assert rows["AUBANK"]["engine"] == "skill"
    assert rows["GVT&D"]["engine"] == "api" and rows["GVT&D"]["rating"] == "Overweight"
    assert rows["SRF"]["rating"] is None and "boom" in rows["SRF"]["error"]
    assert at.pending(day, ["AUBANK", "GVT&D", "SRF"], at.read_decisions(log)) == ["SRF"]   # retried next run


def test_run_registers_the_news_fallback_after_yahoo(tmp_path, monkeypatch):
    fake = _fake_graph(monkeypatch)
    seen = {}
    monkeypatch.setattr(fake, "propagate", lambda self, t, d: (seen.update(self.config), ({}, "Hold"))[1])
    monkeypatch.setenv(at.KEY_ENV, "test-key")
    monkeypatch.chdir(tmp_path)
    day = date(2026, 11, 13)
    assert at.run(day, ["NHPC"], path=tmp_path / "d.jsonl", log=lambda _: None, today=day) == 0
    from tradingagents.dataflows import router

    assert seen["tool_vendors"]["get_news"] == "yfinance,swingbot"
    assert "swingbot" in router.VENDOR_METHODS["get_news"]


def test_run_stops_at_the_spend_limit_and_refuses_without_a_key(tmp_path, monkeypatch):
    _fake_graph(monkeypatch, usd_per_stock=0.6)
    monkeypatch.chdir(tmp_path)
    log, day = tmp_path / "d.jsonl", date(2026, 11, 13)
    monkeypatch.delenv(at.KEY_ENV, raising=False)
    with pytest.raises(SystemExit, match=at.KEY_ENV):
        at.run(day, ["NHPC"], path=log, today=day)
    with pytest.raises(SystemExit, match="days ago"):
        at.run(date(2026, 11, 6), ["NHPC"], path=log, today=day)
    monkeypatch.setenv(at.KEY_ENV, "test-key")
    lines = []
    errors = at.run(day, ["NHPC", "NMDC", "SRF"], path=log, log=lines.append, max_usd=1.0, today=day)
    assert [d["symbol"] for d in at.read_decisions(log)] == ["NHPC", "NMDC"]
    assert errors == 1 and "limit $1.00" in lines[-1]


def test_score_counts_the_api_weeks_only_and_keeps_the_skill_weeks_apart(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rows = [{"date": "2026-11-06", "symbol": "SRF", "rating": "Buy", "engine": "skill"},
            {"date": "2026-11-20", "symbol": "SRF", "rating": "Sell", "engine": "api", "model": "claude-haiku-5-5"},
            {"date": "2026-11-20", "symbol": "NHPC", "rating": "Hold"}]   # no engine field: the API's, as before
    api, skill = at.split_engines(rows)
    assert [d["symbol"] for d in api] == ["SRF", "NHPC"] and [d["date"] for d in skill] == ["2026-11-06"]

    seen = []
    monkeypatch.setattr(at, "score", lambda decisions, data: seen.append([d["date"] for d in decisions]) or {"weeks": [], "calls": []})
    log = tmp_path / "data/state/agents_trial/decisions.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert at.main(["score", "--data", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert seen == [["2026-11-20", "2026-11-20"], ["2026-11-06"]]
    assert "counts the API engine's weeks only, from 2026-11-20" in out
    assert "skill weeks (separate record, not part of the pass bar)" in out
    assert (tmp_path / "runs/agents-trial/report.md").exists() and (tmp_path / "runs/agents-trial/report-skill.md").exists()


def test_report_before_any_api_week_says_the_test_has_not_started():
    text = at.report({"weeks": [], "calls": []}, [])
    assert "which have not started" in text and "Engines: none yet" in text
