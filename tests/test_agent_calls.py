import json

import pandas as pd
import pytest

from swingbot.agent_calls import load, main, record, router_closes, score, settle
from swingbot.models import IST

NOW = pd.Timestamp("2026-11-05 15:45", tz=IST)


def closes(values, start="2026-11-02"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def call(ticker="INFY", rating="Buy", as_of="2026-11-05 15:40"):
    return {"ticker": ticker, "as_of": as_of, "rating": rating, "rationale": "test"}


def test_record_appends_and_normalises(tmp_path):
    log = tmp_path / "d.jsonl"
    d = record(call(ticker="infy.ns", rating="overweight"), log, now=NOW)
    assert (d["ticker"], d["rating"], d["recorded_at"]) == ("INFY", "Overweight", "2026-11-05 15:45")
    assert load(log)[0]["ticker"] == "INFY"


@pytest.mark.parametrize("bad, why", [
    (call(rating="Strong buy"), "not one of"),
    (call(as_of="2026-10-01 15:40"), "forward only"),
    ({"ticker": "INFY", "as_of": "2026-11-05 15:40", "rating": "Buy"}, "no rationale"),
])
def test_record_refuses(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        record(bad, tmp_path / "d.jsonl", now=NOW)


def test_first_call_of_the_day_stands(tmp_path):
    log = tmp_path / "d.jsonl"
    record(call(), log, now=NOW)
    with pytest.raises(ValueError, match="first one stands"):
        record(call(rating="Sell"), log, now=NOW)
    record(call(ticker="TCS"), log, now=NOW)
    assert len(load(log)) == 2


def test_review_is_logged_not_scored(tmp_path):
    log = tmp_path / "d.jsonl"
    record(call(rating="review"), log, now=NOW)
    rows, sm = score(load(log), lambda t: closes([100] * 12))
    assert sm["review"] == 1 and sm["directional"] == 0


def test_settle_from_first_close_after_the_day():
    # Called on Thu 5 Nov: entry is Fri 6 Nov's close, exit 5 sessions later (Fri 13 Nov).
    stock = closes([90, 95, 99, 100, 100, 101, 102, 103, 104, 110])
    bench = closes([50] * 9 + [51])
    out = settle(call(), stock, bench)
    assert (out["entry_day"], out["exit_day"]) == ("2026-11-06", "2026-11-13")
    assert out["raw"] == pytest.approx(0.10)
    assert out["alpha"] == pytest.approx(0.10 - 0.02)
    assert settle(call(rating="Sell"), stock, bench)["signed_alpha"] == pytest.approx(-0.08)
    assert settle(call(rating="Hold"), stock, bench)["signed_alpha"] is None


def test_pending_until_window_and_benchmark_trade():
    assert settle(call(), closes([100] * 9), closes([50] * 9)) is None   # exit day not traded yet
    assert settle(call(), closes([100] * 10), closes([50] * 9)) is None  # benchmark a day behind
    assert settle(call(), closes([100] * 10), closes([50] * 10)) is not None


def test_score_summary_and_missing_prices():
    flat = [100] * 9
    prices = {"NIFTY50": closes([50] * 10), "INFY": closes(flat + [105]), "TCS": closes(flat + [98])}

    def fetch(t):
        if t not in prices:
            raise KeyError(t)
        return prices[t]

    rows, sm = score([call(), call(ticker="TCS", rating="Underweight"), call(ticker="XYZ")], fetch)
    assert [r["status"] for r in rows] == ["settled", "settled", "no prices: 'XYZ'"]
    assert sm["directional"] == 2 and sm["hit_rate"] == 1.0
    assert sm["mean_signed_alpha"] == pytest.approx((0.05 + 0.02) / 2)


def test_cli_record_refusal(tmp_path, capsys):
    f = tmp_path / "x.json"
    f.write_text(json.dumps(call(as_of="2020-01-01 10:00")))
    assert main(["--log", str(tmp_path / "d.jsonl"), "record", str(f)]) == 2
    assert "forward only" in capsys.readouterr().err


def test_scoring_closes_leave_out_a_session_still_trading(monkeypatch):
    live = pd.DataFrame({"close": [100.0, 101.0, 102.0]}, index=pd.to_datetime(["2026-11-04", "2026-11-05", "2026-11-06"]))
    monkeypatch.setattr("swingbot.data.Router.fetch", lambda self, category, t, as_of: ("yfinance", live))
    mid_session = pd.Timestamp("2026-11-06 11:00", tz=IST)
    assert list(router_closes("INFY", mid_session)) == [100.0, 101.0]
    assert list(router_closes("INFY", pd.Timestamp("2026-11-06 16:00", tz=IST))) == [100.0, 101.0, 102.0]


# ---------------------------------------------------------------- the API run

FRIDAY = pd.Timestamp("2026-11-13 16:30", tz=IST)


class FakeRouter:
    """Serves text per category and a flat price history; counts nothing as a gap except fundamentals."""

    def text(self, category, ticker, as_of, **kw):
        if category == "fundamentals":
            return f"WITHHELD: fundamentals for {ticker} (test)"
        return f"{category} of {ticker} to {as_of:%Y-%m-%d %H:%M}"

    def fetch(self, category, ticker, as_of):
        days = pd.bdate_range("2026-01-01", "2026-11-13")
        bars = pd.DataFrame({c: 100.0 for c in ("open", "high", "low", "close")}, index=days)
        return "fake", bars.assign(volume=1000)


def fake_model(final="Rating: Overweight\nSolid.\nEntry: Rs. 1,450.50\nStop: ₹1,400\nRationale: trend and results agree."):
    seen = []

    def complete(system, user):
        role = system.rsplit("Your role: ", 1)[1].split(".")[0]
        seen.append((role, user))
        text = {"Trader": "Action: Buy\nEntry 1450.50, stop 1400, 2 x ATR14.", "Portfolio Manager": final}
        return text.get(role, f"{role} says so"), 1000, 200

    return complete, seen


def run_api(tmp_path, monkeypatch, tickers=("INFY",), **kw):
    from swingbot import agent_calls, regime

    monkeypatch.setattr(regime, "from_router", lambda router, as_of: type("M", (), {"text": lambda self: "Market mood: NEUTRAL"})())
    lines = []
    kw.setdefault("now", FRIDAY)
    failed = agent_calls.run(tickers, tmp_path / "d.jsonl", router=FakeRouter(), log=lines.append, **kw)
    return failed, lines, load(tmp_path / "d.jsonl")


def test_api_run_plays_every_role_in_order_and_records(tmp_path, monkeypatch):
    complete, seen = fake_model()
    failed, lines, log = run_api(tmp_path, monkeypatch, complete=complete)
    assert failed == 0 and [role for role, _ in seen] == [
        "Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst", "Bull Researcher",
        "Bear Researcher", "Research Manager", "Trader", "Aggressive Risk Analyst",
        "Conservative Risk Analyst", "Neutral Risk Analyst", "Portfolio Manager"]
    d = log[0]
    assert (d["ticker"], d["rating"], d["action"], d["entry"], d["stop"]) == ("INFY", "Overweight", "Buy", 1450.5, 1400.0)
    assert d["rationale"] == "trend and results agree." and d["as_of"] == "2026-11-13 16:30"
    assert (d["engine"], d["model"], d["llm_calls"], d["tokens_in"], d["tokens_out"]) == ("api", "claude-haiku-5-5", 12, 12000, 2400)
    assert d["usd"] == pytest.approx(12 * (1000 * 0.10 + 200 * 0.50) / 1e6, abs=1e-4)
    assert d["gaps"][0] == "Social sentiment: NOT_CONFIGURED" and d["gaps"][1].startswith("WITHHELD")
    assert d["atr14"] == 0.0 and "Bear: Bear Researcher says so" in d["reports"]["debate"]
    assert json.loads((tmp_path / "2026-11-13" / "INFY.json").read_text())["rating"] == "Overweight"


def test_api_run_shows_each_role_only_what_tradingagents_gives_it(tmp_path, monkeypatch):
    complete, seen = fake_model()
    run_api(tmp_path, monkeypatch, complete=complete)
    shown = dict(seen)
    assert "technicals of INFY" in shown["Market Analyst"] and "news of INFY" not in shown["Market Analyst"]
    assert "Market mood: NEUTRAL" in shown["News Analyst"] and "filings of INFY" in shown["News Analyst"]
    # After the analysts, nobody sees raw data: only the reports and the debate so far.
    for role in ("Bull Researcher", "Bear Researcher", "Research Manager", "Trader", "Neutral Risk Analyst"):
        assert "technicals of INFY" not in shown[role] and "Market Analyst says so" in shown[role]
    assert "Bull Researcher says so" in shown["Bear Researcher"] and "Bear Researcher" not in shown["Bull Researcher"]
    pm = shown["Portfolio Manager"]
    assert "Neutral Risk Analyst says so" in pm and "No earlier calls on this stock." in pm
    assert "Market Analyst says so" not in pm


def test_api_run_unreadable_rating_is_review_and_the_first_call_stands(tmp_path, monkeypatch):
    complete, seen = fake_model(final="I can't decide between Hold and Sell.")
    failed, lines, log = run_api(tmp_path, monkeypatch, complete=complete)
    assert failed == 0 and log[0]["rating"] == "REVIEW" and log[0]["entry"] is None
    again, seen2 = fake_model()
    failed, lines, log = run_api(tmp_path, monkeypatch, complete=again)
    assert "already has a call" in lines[-1] and seen2 == [] and len(log) == 1   # no second call, no spend


def test_api_run_stops_at_the_spend_limit_and_survives_a_failed_stock(tmp_path, monkeypatch):
    complete, _ = fake_model()

    def flaky(system, user):
        if "TCS" in system:
            raise RuntimeError("overloaded")
        return complete(system, user)

    failed, lines, log = run_api(tmp_path, monkeypatch, tickers=("TCS", "infy.ns", "ITC"), complete=flaky,
                                 max_usd=0.002)
    assert [d["ticker"] for d in log] == ["INFY"] and failed == 2
    assert "no call (RuntimeError: overloaded)" in lines[0] and "limit $0.00" in lines[-1]


def test_api_run_needs_a_key_and_reads_it_from_dot_env(tmp_path, monkeypatch):
    from swingbot import agent_calls

    monkeypatch.delenv(agent_calls.KEY_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match=agent_calls.KEY_ENV):
        agent_calls.run(["INFY"], tmp_path / "d.jsonl", router=FakeRouter(), now=FRIDAY)
    (tmp_path / ".env").write_text("OTHER=1\nexport ANTHROPIC_API_KEY='from-file'\n")
    assert agent_calls.api_key() == "from-file"
    monkeypatch.setenv(agent_calls.KEY_ENV, "from-env")
    assert agent_calls.api_key() == "from-env"


def test_anthropic_complete_keeps_the_text_and_drops_the_thinking(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    from types import SimpleNamespace as NS
    from swingbot import agent_calls

    sent = {}

    class FakeClient:
        def __init__(self, api_key):
            sent["key"] = api_key
            self.messages = self

        def create(self, **kw):
            sent.update(kw)
            return NS(content=[NS(type="thinking", thinking="hmm"), NS(type="text", text="Rating: Hold")],
                      usage=NS(input_tokens=11, output_tokens=7), stop_reason="max_tokens")

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    text, t_in, t_out = agent_calls.anthropic_complete("k")("sys", "user")
    assert text == "Rating: Hold\n\n(cut off at the length limit)" and (t_in, t_out) == (11, 7)
    assert sent["key"] == "k" and sent["model"] == "claude-haiku-5-5" and sent["system"] == "sys"
    assert "temperature" not in sent and sent["messages"] == [{"role": "user", "content": "user"}]


def test_watchlist_is_the_preregistered_ten():
    from swingbot.agent_calls import WATCHLIST

    assert WATCHLIST == ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "BHARTIARTL",
                         "SUNPHARMA", "MARUTI")


def test_api_run_makes_no_call_without_prices(tmp_path, monkeypatch):
    class NoPrices(FakeRouter):
        def text(self, category, ticker, as_of, **kw):
            if category == "technicals":
                return f"NO_DATA: no technicals for {ticker}"
            return super().text(category, ticker, as_of, **kw)

    from swingbot import agent_calls, regime

    monkeypatch.setattr(regime, "from_router", lambda router, as_of: type("M", (), {"text": lambda self: "mood"})())
    complete, seen = fake_model()
    lines = []
    failed = agent_calls.run(["INFY"], tmp_path / "d.jsonl", router=NoPrices(), complete=complete,
                             now=FRIDAY, log=lines.append)
    assert failed == 1 and seen == [] and load(tmp_path / "d.jsonl") == []     # nothing spent, nothing logged
    assert "no prices to decide from" in lines[-1]


def test_api_run_counts_what_a_failed_stock_spent(tmp_path, monkeypatch):
    complete, _ = fake_model()

    def fails_at_the_end(system, user):
        if "Portfolio Manager" in system:
            raise RuntimeError("overloaded")
        return complete(system, user)

    # 11 paid roles on TCS (about $0.0022) pass the limit, so INFY is not started.
    failed, lines, log = run_api(tmp_path, monkeypatch, tickers=("TCS", "INFY"), complete=fails_at_the_end,
                                 max_usd=0.002)
    assert failed == 2 and log == [] and "not run" in lines[-1]


def test_api_run_is_for_fridays_unless_told_otherwise(tmp_path, monkeypatch):
    monday = pd.Timestamp("2026-11-16 16:30", tz=IST)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)   # no .env here, so reaching the key check would exit
    failed, lines, log = run_api(tmp_path, monkeypatch, tickers=("INFY", "TCS"), now=monday)
    assert failed == 2 and log == [] and "today is Monday" in lines[0] and "--any-day" in lines[0]
    complete, _ = fake_model()
    failed, _, log = run_api(tmp_path, monkeypatch, now=monday, any_day=True, complete=complete)
    assert failed == 0 and [d["ticker"] for d in log] == ["INFY"]


def test_api_run_skips_a_stock_called_in_the_last_five_days(tmp_path, monkeypatch):
    log = tmp_path / "d.jsonl"
    log.write_text(json.dumps(call(as_of="2026-11-10 16:30")) + "\n" +
                   json.dumps(call(ticker="TCS", as_of="2026-11-06 16:30")) + "\n")   # 3 days, 7 days before
    complete, seen = fake_model()
    failed, lines, rows = run_api(tmp_path, monkeypatch, tickers=("INFY", "TCS"), complete=complete)
    assert failed == 0 and [d["ticker"] for d in rows if d["as_of"].startswith("2026-11-13")] == ["TCS"]
    assert "INFY: last call was 2026-11-10" in lines[0] and len(seen) == 12


def test_score_keeps_the_api_test_apart_from_skill_calls(tmp_path, monkeypatch, capsys):
    from swingbot import agent_calls

    flat = [100] * 9
    prices = {"NIFTY50": closes([50] * 10), "INFY": closes(flat + [105]), "TCS": closes(flat + [98])}
    monkeypatch.setattr(agent_calls, "router_closes", lambda t: prices[t])
    log = tmp_path / "d.jsonl"
    rows = [call(), {**call(ticker="TCS", rating="Underweight"), "engine": "api"}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert main(["--log", str(log), "score"]) == 0
    out = capsys.readouterr().out
    test, skill = out.split("Earlier skill calls")
    assert "TCS" in test and "INFY" not in test and "1 calls: 1 settled" in test
    assert "INFY" in skill and "TCS" not in skill
    # One stock's history stays whole, whichever engine made each call (the Portfolio Manager's memory).
    assert main(["--log", str(log), "score", "--ticker", "INFY"]) == 0
    assert "Earlier skill calls" not in capsys.readouterr().out
    # No API call yet: the test says so instead of borrowing the skill's.
    log.write_text(json.dumps(call()) + "\n")
    main(["--log", str(log), "score"])
    assert "none yet" in capsys.readouterr().out.split("Earlier skill calls")[0]
