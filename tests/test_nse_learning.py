"""Step 8 wired for NSE: replay on eod2-style data, and shadow picks settled at each booking."""

import json

import pandas as pd
import pytest
from test_momentum import KNOWN_2025, write_stocks

from swingbot import momentum
from swingbot.markets.skills import NseMembers, Skills, SkillRefused, nse_month_ends, nse_shadow
from swingbot.risk.calendar import NSECalendar


@pytest.fixture(autouse=True)
def small_rule(monkeypatch):
    monkeypatch.setattr(momentum, "RANKS", (2, 6))
    monkeypatch.setattr(momentum, "TOP", 2)
    monkeypatch.setattr(momentum, "MIN_HISTORY", 30)


def to_shadow(s: Skills, key: str) -> None:
    """Test shortcut past the replay gate; the gate itself is covered in test_skills.py."""
    s._append("nse", {"kind": "skill_status", "skill": key, "status": "shadow", "by": "Harsha", "why": "test"})


def test_the_replay_universe_is_the_trials_own_bucket(tmp_path):
    write_stocks(tmp_path / "d")
    px, val = momentum.load(tmp_path / "d", since="2023-01-01")
    asof = pd.Timestamp("2025-07-31")
    assert NseMembers(px, val).at(asof.date()) == momentum.select(px, val, asof)[0]


def test_nse_candidates_replay_against_the_approved_rule_and_cant_ask_for_a_trend_gate(tmp_path):
    write_stocks(tmp_path / "d", end="2026-08-31")
    px, val = momentum.load(tmp_path / "d", since="2023-01-01")
    s = Skills(tmp_path / "state")
    with pytest.raises(SkillRefused, match="no trend gate"):
        s.propose("nse", "nse-momentum", {"trend_gate": True}, "test", ["x"], owner="Harsha")
    key = s.propose("nse", "nse-momentum", {"lookback": 3}, "test", ["a lesson"], owner="Harsha")
    rec = s.replay("nse", key, px, NseMembers(px, val), panel_id="eod2-test")
    assert rec["against"] == "nse-momentum@v1" and rec["verdict"] in ("pass", "fail") and rec["years"] >= 1
    assert s.all("nse")[key]["replays"][-1]["panel"] == "eod2-test"


def test_each_booking_picks_for_shadow_skills_and_settles_last_month(tmp_path):
    write_stocks(tmp_path / "d", end="2025-09-30")
    state = tmp_path / "state"
    s = Skills(state)
    key = s.propose("nse", "nse-momentum", {"lookback": 3, "n": 2}, "test", ["a lesson"], owner="Harsha")
    to_shadow(s, key)
    ledger = state / "momentum_trial.json"
    done = momentum.update(tmp_path / "d", ledger, NSECalendar({2025: set()}), start="2025-07-01")
    months = json.loads(ledger.read_text())["months"]
    assert [m["date"] for m in done] == ["2025-07-31", "2025-08-29", "2025-09-30"]
    assert all(len(m["shadow"][key]) == 2 for m in months)  # the candidate's own n, not the trial's
    rec = s.all("nse")[key]["shadow"]
    assert set(rec) == {"2025-07", "2025-08"}  # September's picks settle at the October booking
    assert all(isinstance(v["ret"], float) and isinstance(v["current"], float) for v in rec.values())


def test_no_shadow_skill_means_no_shadow_picks(tmp_path):
    write_stocks(tmp_path / "d")
    px, val = momentum.load(tmp_path / "d", since="2023-01-01")
    ledger = {"months": [{"date": "2025-07-31", "picks": ["S4", "S5"]}]}
    assert nse_shadow(Skills(tmp_path), px, val, ledger, ledger["months"][0]) == {}
    assert nse_month_ends(px).index[-1] == pd.Timestamp("2025-08-29")


def test_known_2025_calendar_is_reused():  # keeps the import honest for readers of this file
    assert KNOWN_2025.knows(2025)
