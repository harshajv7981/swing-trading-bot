"""Step 8: versioned skills per market, candidates from evidence, replay and approval gates."""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from swingbot.markets.skills import Skills, SkillRefused, gate
from swingbot.us_research import Membership


def test_each_market_starts_with_its_pre_registered_rule_approved(tmp_path):
    s = Skills(tmp_path)
    us, nse = s.all("us"), s.all("nse")
    assert us["us-momentum@v1"]["status"] == "approved" and us["us-momentum@v1"]["params"]["lookback"] == 12
    assert "us-momentum-prereg.md" in us["us-momentum@v1"]["approved_by"]
    assert nse["nse-momentum@v1"]["params"]["lookback"] == 6 and not set(nse) & set(us)


def test_a_candidate_needs_evidence_and_new_settings(tmp_path):
    s = Skills(tmp_path)
    with pytest.raises(SkillRefused, match="evidence"):
        s.propose("us", "us-momentum", {"lookback": 6}, "try 6", [], owner="claude")
    key = s.propose("us", "us-momentum", {"lookback": 6}, "M6 led in the study", ["us-research study abc"],
                    owner="claude")
    assert key == "us-momentum@v2" and s.all("us")[key]["status"] == "candidate"
    assert s.all("us")[key]["params"] == {"lookback": 6, "skip": 1, "n": 20, "band": 0.25, "trend_gate": False}
    with pytest.raises(SkillRefused, match="same settings"):
        s.propose("us", "us-momentum", {"lookback": 6}, "again", ["x"], owner="claude")
    with pytest.raises(SkillRefused, match="unknown"):
        s.propose("us", "us-momentum", {"lookbak": 6}, "typo", ["x"], owner="claude")


def test_the_replay_gate_needs_more_return_no_deeper_fall_and_steady_years():
    cur = {"cagr": 0.12, "max_dd": -0.30}
    assert gate({"cagr": 0.15, "max_dd": -0.26}, cur, years_won=7, years=10)[0] == "pass"
    verdict, why = gate({"cagr": 0.15, "max_dd": -0.40}, cur, years_won=7, years=10)
    assert verdict == "fail" and any("fall" in w for w in why)
    assert gate({"cagr": 0.11, "max_dd": -0.20}, cur, years_won=7, years=10)[0] == "fail"
    assert gate({"cagr": 0.15, "max_dd": -0.26}, cur, years_won=5, years=10)[0] == "fail"


def panel():
    days = pd.bdate_range("2016-01-04", "2019-12-31")
    rng = np.random.default_rng(1)
    cols = {s: 100 * np.cumprod(1 + rng.normal(mu, 0.012, len(days)))
            for s, mu in {"A": 0.0008, "B": 0.0004, "C": 0.0, "D": -0.0004, "E": 0.0006,
                          "SPY": 0.0004, "RSP": 0.0003, "SPMO": 0.0005}.items()}
    return pd.DataFrame(cols, index=days), Membership(["A", "B", "C", "D", "E"], [])


def test_replay_records_a_verdict_and_gates_the_move_to_shadow(tmp_path):
    s = Skills(tmp_path)
    key = s.propose("us", "us-momentum", {"lookback": 6, "n": 2}, "test", ["study"], owner="claude")
    with pytest.raises(SkillRefused, match="replay"):
        s.decide("us", key, "shadow", by="harsha", why="looks good")
    daily, members = panel()
    rec = s.replay("us", key, daily, members, panel_id="sha-test")
    assert rec["verdict"] in ("pass", "fail") and rec["panel"] == "sha-test"
    assert set(rec["candidate"]) >= {"cagr", "max_dd"} and rec["years"] >= 3
    assert s.all("us")[key]["replays"][-1]["verdict"] == rec["verdict"]
    if rec["verdict"] == "fail":
        with pytest.raises(SkillRefused, match="replay"):
            s.decide("us", key, "shadow", by="harsha", why="x")


def test_shadow_months_and_a_person_are_needed_before_approval(tmp_path):
    s = Skills(tmp_path)
    key = s.propose("us", "us-momentum", {"lookback": 6}, "test", ["study"], owner="claude")
    s._append("us", {"kind": "skill_replayed", "skill": key, "verdict": "pass", "panel": "p", "why": []})
    with pytest.raises(SkillRefused, match="person"):
        s.decide("us", key, "shadow", by="claude", why="passed")
    s.decide("us", key, "shadow", by="harsha", why="passed the screen")
    with pytest.raises(SkillRefused, match="6 shadow months"):
        s.decide("us", key, "approve", by="harsha", why="early")
    for m in range(1, 7):
        s.record_shadow("us", key, f"2027-{m:02d}", ret=0.01, current=0.005)
    s.decide("us", key, "approve", by="harsha", why="six months ahead")
    skills = s.all("us")
    assert skills[key]["status"] == "approved" and skills["us-momentum@v1"]["status"] == "retired"
    assert "superseded by us-momentum@v2" in skills["us-momentum@v1"]["history"][-1]


def test_candidates_are_generated_from_the_study_once(tmp_path):
    s = Skills(tmp_path)
    study = tmp_path / "study.json"
    study.write_text(json.dumps({"answers": {"q2_m6_vs_m12": "M6 higher", "q3_m12_trend_vs_m12": "does not help"},
                                 "whole": {"M6": {"cagr": 0.157}, "M12": {"cagr": 0.127}}}))
    made = s.generate_from_study("us", study, panel_id="sha1")
    assert [s.all("us")[k]["params"]["lookback"] for k in made] == [6]
    assert s.generate_from_study("us", study, panel_id="sha1") == []


def test_nse_replay_is_not_supported_yet(tmp_path):
    s = Skills(tmp_path)
    key = s.propose("nse", "nse-momentum", {"lookback": 12}, "test", ["x"], owner="claude")
    daily, members = panel()
    with pytest.raises(SkillRefused, match="not supported"):
        s.replay("nse", key, daily, members, panel_id="p")
    assert date(2026, 11, 7)
