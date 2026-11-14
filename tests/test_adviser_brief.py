import json

import pytest
from test_momentum import KNOWN_2025, write_stocks

from swingbot import adviser_brief, momentum


@pytest.fixture(autouse=True)
def small_rule(monkeypatch):
    monkeypatch.setattr(momentum, "RANKS", (2, 6))
    monkeypatch.setattr(momentum, "TOP", 2)
    monkeypatch.setattr(momentum, "MIN_HISTORY", 30)


@pytest.fixture
def booked(tmp_path):
    write_stocks(tmp_path / "d")
    ledger = tmp_path / "ledger.json"
    momentum.update(tmp_path / "d", ledger, KNOWN_2025, start="2025-07-01")
    return tmp_path / "d", ledger


def test_the_brief_lists_the_booked_picks_with_their_data_and_the_caveats(booked):
    data, ledger_path = booked
    ledger = json.loads(ledger_path.read_text())
    px, val = momentum.load(data, since="2023-01-01")
    aug = ledger["months"][1]
    text = adviser_brief.brief(ledger, px, val, aug, {"S5": "IT"})
    assert "not a recommendation" in text and "SEBI-registered" in text
    for sym in aug["picks"]:
        assert f"| {sym} |" in text
    assert "| S5 | IT |" in text and "2 months" in text  # held in July and August
    assert "Changes since 2025-07-31" in text
    assert "Questions to ask the adviser" in text and "ASM/GSM" in text
    jul = adviser_brief.brief(ledger, px, val, ledger["months"][0], {})
    assert "Changes since" not in jul and "| new |" in jul


def test_write_names_the_file_by_month_and_refuses_an_unbooked_one(booked, tmp_path):
    data, ledger = booked
    path = adviser_brief.write(data, "2025-08", ledger, tmp_path / "out")
    assert path.name == "brief-2025-08.md" and "August 2025" in path.read_text()
    assert adviser_brief.write(data, None, ledger, tmp_path / "out").name == "brief-2025-08.md"
    with pytest.raises(ValueError, match="no booking for 2025-05"):
        adviser_brief.write(data, "2025-05", ledger, tmp_path / "out")


def test_a_brief_that_no_longer_matches_the_booking_is_refused(booked):
    data, ledger_path = booked
    ledger = json.loads(ledger_path.read_text())
    ledger["months"][1]["picks"] = ["S0", "S1"]
    px, val = momentum.load(data, since="2023-01-01")
    with pytest.raises(ValueError, match="no longer gives the booked picks"):
        adviser_brief.brief(ledger, px, val, ledger["months"][1], {})
