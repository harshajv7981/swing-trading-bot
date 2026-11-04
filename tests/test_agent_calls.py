import json

import pandas as pd
import pytest

from swingbot.agent_calls import load, main, record, score, settle
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
