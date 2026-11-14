import json

import pandas as pd

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


def ledger_with(results, paper, ew, fund):
    months = [{"date": "2026-10-30", "value": 1e6, "ew": {"level": 1.0}, "indexes": {adviser_brief.FUND: 100.0}}]
    for k in range(results):
        months.append({"date": f"2027-0{k + 1}-28", "value": 1e6 * (1 + paper), "ew": {"level": 1 + ew},
                       "indexes": {adviser_brief.FUND: 100 * (1 + fund)}})
    return {"capital": 1e6, "months": months}


def test_the_paper_gate_needs_six_results_a_gain_and_both_benchmarks_beaten():
    ok, why = adviser_brief.gate(*(lambda led: (led, led["months"][-1]))(ledger_with(6, 0.10, 0.05, 0.08)))
    assert ok and all(w.startswith("pass") for w in why)
    for args, missing in [((5, 0.10, 0.05, 0.08), "5 of 6"), ((6, -0.01, -0.05, -0.08), "paper account"),
                          ((6, 0.10, 0.12, 0.08), "equal weight"), ((6, 0.10, 0.05, 0.11), "Momentum 50")]:
        led = ledger_with(*args)
        ok, why = adviser_brief.gate(led, led["months"][-1])
        assert not ok and any(w.startswith("not yet") and missing in w for w in why)


def test_a_cleared_brief_gives_actions_stops_and_sizes(tmp_path, monkeypatch):
    def ohlc(df):
        return df.assign(High=df["Close"] * 1.01, Low=df["Close"] * 0.99)

    write_stocks(tmp_path / "d", extra={k: ohlc for k in range(10)})
    ledger_path = tmp_path / "ledger.json"
    momentum.update(tmp_path / "d", ledger_path, KNOWN_2025, start="2025-07-01")
    ledger = json.loads(ledger_path.read_text())
    jul, aug = ledger["months"]
    jul["picks"] = ["S4", "S9"]  # so August keeps S4, adds S5 and drops S9
    for m, (paper, fund) in ((jul, (0.0, 100.0)), (aug, (0.10, 101.0))):
        m["value"], m["ew"]["level"] = ledger["capital"] * (1 + paper), 1.02
        m["indexes"][adviser_brief.FUND] = fund
    monkeypatch.setattr(adviser_brief, "GATE_MONTHS", 1)
    px, val = momentum.load(tmp_path / "d", since="2023-01-01")
    text = adviser_brief.brief(ledger, px, val, aug, {}, tmp_path / "d", cap=100_000)
    assert "CLEARED FOR ADVISER REVIEW" in text
    assert "| HOLD | S4 |" in text and "| BUY | S5 |" in text
    assert "**SELL (dropped from the list):** S9 at about" in text
    s5 = px["S5"].iloc[-1] if px.index[-1] <= pd.Timestamp(aug["date"]) else px.loc[aug["date"], "S5"]
    assert f"| {int(50_000 // s5):,} | new |" in text  # Rs 1 lakh cap / 2 picks
    row = next(line for line in text.splitlines() if "| BUY | S5 |" in line)
    stop = float(row.split("|")[12].split("(")[0].replace(",", ""))
    assert 0 < stop < s5 and "information only" in text


def test_a_brief_that_is_not_cleared_has_no_actions(booked):
    data, ledger_path = booked
    ledger = json.loads(ledger_path.read_text())
    px, val = momentum.load(data, since="2023-01-01")
    text = adviser_brief.brief(ledger, px, val, ledger["months"][1], {}, data)
    assert "PAPER ONLY" in text and "| BUY |" not in text and "| paper |" in text


def test_the_brief_reports_the_learning_loop(booked):
    from swingbot.markets.memory import Memory
    from swingbot.markets.skills import Skills

    data, ledger_path = booked
    state = ledger_path.parent
    momentum.record_memory(ledger_path)  # July settles when August books
    sk = Skills(state)
    key = sk.propose("nse", "nse-momentum", {"lookback": 3}, "a repeated mistake", ["episode"], owner="Harsha")
    july = next(k for k in Memory(state).episodes("nse") if k.endswith("2025-07"))
    lesson = Memory(state).propose("nse", "strategy", "Mid-cap momentum lags in sharp reversals", [july],
                                   "low", by="Harsha")
    Memory(state).decide("nse", lesson, "approve", by="Harsha", why="seen twice")
    path = adviser_brief.write(data, "2025-08", ledger_path, state / "out")
    text = path.read_text()
    assert "## How the system is learning" in text and "`nse-momentum@v1`" in text
    assert "1 settled month" in text and "Lessons approved:** 1" in text and "sharp reversals" in text
    assert f"Candidate `{key}`" in text and "candidate, replay not replayed, 0 of 6 shadow months" in text
    assert "your sign-off" in text


def test_paper_only_briefs_show_stops_with_the_information_only_note(tmp_path):
    def ohlc(df):
        return df.assign(High=df["Close"] * 1.01, Low=df["Close"] * 0.99)

    write_stocks(tmp_path / "d", extra={k: ohlc for k in range(10)})
    ledger_path = tmp_path / "ledger.json"
    momentum.update(tmp_path / "d", ledger_path, KNOWN_2025, start="2025-07-01")
    ledger = json.loads(ledger_path.read_text())
    px, val = momentum.load(tmp_path / "d", since="2023-01-01")
    text = adviser_brief.brief(ledger, px, val, ledger["months"][1], {}, tmp_path / "d")
    assert "PAPER ONLY" in text and "| BUY |" not in text and "information only" in text
    row = next(line for line in text.splitlines() if "| paper | S5 |" in line)
    assert "(-" in row.split("|")[12]  # a stop level below the close, not a dash
