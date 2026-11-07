"""Phase 1 US history study (docs/research/us-research-prereg.md)."""

import json
from datetime import date

import pandas as pd
import pytest

from swingbot.us_momentum import AlpacaData, replay
from swingbot.us_research import (
    Membership,
    ew_returns,
    load_panel,
    parse_changes,
    summary,
    trend_gate,
    write_panel,
)

CHANGES = """
<table id="changes"><tr><th>Date</th><th colspan=2>Added</th><th colspan=2>Removed</th><th>Reason</th></tr>
<tr><th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th></tr>
<tr><td rowspan=2>September 22, 2025</td><td>APP</td><td>AppLovin</td><td>ENPH</td><td>Enphase</td><td>cap</td></tr>
<tr><td>HOOD</td><td>Robinhood</td><td>CZR</td><td>Caesars</td><td>cap</td><td><sup>[3]</sup></td></tr>
<tr><td>March 18, 2024</td><td>SMCI</td><td>Super Micro</td><td>WHR</td><td>Whirlpool</td><td>cap</td></tr>
<tr><td>June 1, 2023</td><td></td><td></td><td>OLD</td><td>Old Co</td><td>acquired</td></tr>
</table>"""


def test_members_are_rebuilt_for_each_date_from_the_change_table():
    """The table moved to "Historical components of the S&P 500" in 2026 and gained a Refs column, so a
    row that shares the date above has six cells: the date is recognised by parsing, not by counting."""
    changes = parse_changes(CHANGES)
    assert changes[0] == (date(2025, 9, 22), "APP", "ENPH")
    assert changes[1] == (date(2025, 9, 22), "HOOD", "CZR")       # rowspan date reused
    m = Membership(["AAPL", "APP", "HOOD", "SMCI"], changes)
    assert m.at(date(2025, 9, 30)) == ["AAPL", "APP", "HOOD", "SMCI"]
    assert m.at(date(2025, 9, 22)) == ["AAPL", "APP", "HOOD", "SMCI"]      # in effect on its own date
    assert m.at(date(2025, 9, 21)) == ["AAPL", "CZR", "ENPH", "SMCI"]
    assert m.at(date(2024, 1, 31)) == ["AAPL", "CZR", "ENPH", "WHR"]
    assert m.at(date(2023, 5, 31)) == ["AAPL", "CZR", "ENPH", "OLD", "WHR"]
    assert m.ever(date(2023, 1, 1)) == ["AAPL", "APP", "CZR", "ENPH", "HOOD", "OLD", "SMCI", "WHR"]


def month_closes(cols: dict[str, list[float]]) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.date_range("2016-01-29", periods=n, freq="BME"))


def test_replay_takes_dated_members_a_lookback_and_a_trend_gate():
    up = [100 * 1.02 ** i for i in range(16)]
    flat = [100.0] * 16
    m = month_closes({"A": up, "B": flat, "SPY": up})
    rows = replay(m, members=lambda d: ["A"] if d == m.index[12] else ["B"], first=m.index[12], last=m.index[13],
                  n=1, lookback=6, cost=0.0)
    assert [r["picks"] for r in rows] == [["A"], ["B"]]
    assert trend_gate(m["SPY"])(m.index[12]) is True
    down = month_closes({"A": up, "SPY": up[:12] + [50.0] * 4})
    cash = replay(down, members=["A"], first=down.index[12], last=down.index[13], n=1, cost=0.01,
                  gate=trend_gate(down["SPY"]))
    assert cash[0]["picks"] == [] and cash[0]["ret"] == 0.0 and cash[0]["cost"] == 0.0


def test_equal_weight_uses_each_month_ends_members_with_both_closes():
    m = month_closes({"A": [100, 110, 121], "B": [100, 90, None], "C": [None, 100, 100]})
    ew = ew_returns(m, lambda d: ["A", "B", "C"])
    assert ew.iloc[0] == pytest.approx((0.10 - 0.10) / 2)        # C has no start close
    assert ew.iloc[1] == pytest.approx((0.10 + 0.0) / 2)         # B has no next close; C is flat
    assert len(ew) == 2


def test_summary_gives_cagr_drawdown_and_t_stat_over_a_baseline():
    rets = pd.Series([0.10, -0.20, 0.05, 0.10], index=pd.date_range("2017-01-31", periods=4, freq="ME"))
    base = pd.Series([0.0] * 4, index=rets.index)
    s = summary(rets, base)
    growth = 1.1 * 0.8 * 1.05 * 1.1
    assert s["total"] == pytest.approx(growth - 1)
    assert s["cagr"] == pytest.approx(growth ** 3 - 1)
    assert s["max_dd"] == pytest.approx(0.8 - 1)
    diff = rets - base
    assert s["t_vs_base"] == pytest.approx(diff.mean() / diff.std() * 2)


def test_the_frozen_panel_has_a_stable_hash_and_reads_back(tmp_path):
    daily = pd.DataFrame({"B": [1.0, 2.0], "A": [3.123456789, None]},
                         index=pd.to_datetime(["2016-01-04", "2016-01-05"]))
    m1 = write_panel(daily, tmp_path / "p1", {"feed": "sip"})
    m2 = write_panel(daily, tmp_path / "p2", {"feed": "sip"})
    assert m1["sha256"] == m2["sha256"] and m1["symbols"] == 2
    back, manifest = load_panel(tmp_path / "p1")
    assert list(back.columns) == ["A", "B"] and back.loc["2016-01-04", "A"] == pytest.approx(3.123457)
    assert manifest["feed"] == "sip"
    (tmp_path / "p1" / "closes.csv.gz").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        load_panel(tmp_path / "p1")


def test_market_data_waits_out_the_rate_limit():
    answers = [(429, b"too many"),
               (200, json.dumps({"bars": {"A": [{"t": "2016-01-04T05:00:00Z", "c": 1.0}]}}).encode())]
    slept = []
    data = AlpacaData("k", "s", transport=lambda *a: answers.pop(0), sleep=slept.append)
    assert data.daily_closes(["A"], date(2016, 1, 1), date(2016, 1, 5))["A"].iloc[0] == 1.0
    assert slept and slept[0] > 0


def test_the_whole_study_runs_on_a_small_panel_and_reads_its_answers():
    import numpy as np

    from swingbot.us_research import report, run_study

    days = pd.bdate_range("2016-01-04", "2018-12-31")
    rng = np.random.default_rng(0)
    cols = {s: 100 * np.cumprod(1 + rng.normal(mu, 0.01, len(days)))
            for s, mu in {"A": 0.001, "B": 0.0005, "C": 0.0, "D": -0.0005, "SPY": 0.0004, "RSP": 0.0003,
                          "SPMO": 0.0005}.items()}
    daily = pd.DataFrame(cols, index=days)
    members = Membership(["A", "B", "C"], [(date(2017, 6, 1), "C", "D")])
    res = run_study(daily, members)
    assert set(res["answers"]) == {"q1_m12_vs_ew", "q2_m6_vs_m12", "q3_trend_vs_spy", "q3_m12_trend_vs_m12"}
    assert res["first_held"] == "2017-02-28" and res["last_held"] == "2018-12-31"
    assert res["whole"]["M12"]["months"] == 23 and "cagr_stress" in res["whole"]["M12"]
    text = report(res, {"symbols": 7, "first": "2016-01-04", "last": "2018-12-31", "feed": "sip",
                        "fetched_utc": "x", "sha256": "0" * 64})
    assert "Answers, read as pre-registered" in text and "| 2018 |" in text
