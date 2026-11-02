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
