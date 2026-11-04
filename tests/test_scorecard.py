import math

import pytest

from swingbot import momentum, scorecard


def ledger(values, ew, midcap, notes=None):
    """A ledger with one month per value; every index but the Midcap 150 missing."""
    months = []
    for i, (d, v, e, m) in enumerate(zip(["2025-09-30", "2025-10-31", "2025-11-28", "2025-12-31"], values, ew, midcap)):
        months.append({"date": d, "value": v, "ew": {"level": e}, "indexes": {"Nifty Midcap 150": m},
                       "turnover": 0.4, "costs": 100.0, "notes": (notes or {}).get(i, [])})
    return {"start": "2025-09-30", "capital": 100.0, "months": months}


def test_levels_start_at_one_and_end_at_a_gap():
    lv = scorecard.levels(ledger([100, 110, 99, 120], [1, 1.05, 1.0, 1.1], [50, 55, 60, 66]))
    assert list(lv[scorecard.PAPER]) == pytest.approx([1.0, 1.1, 0.99, 1.2])
    assert list(lv["Nifty Midcap 150"]) == pytest.approx([1.0, 1.1, 1.2, 1.32])
    assert lv["Gold ETF (GOLDBEES)"].isna().all() and lv[scorecard.MIX].isna().all()
    led = ledger([100, 110, 99, 120], [1] * 4, [50, 55, 60, 66])
    del led["months"][1]["indexes"]["Nifty Midcap 150"]
    assert scorecard.levels(led)["Nifty Midcap 150"].iloc[1:].isna().all()  # not stitched back after the gap


def test_stats_on_a_known_curve():
    s = scorecard.stats(scorecard.levels(ledger([100, 110, 99, 120], [1] * 4, [1] * 4))[scorecard.PAPER])
    r = [0.10, -0.10, 120 / 99 - 1]
    assert s["months"] == 3 and s["total"] == pytest.approx(0.2)
    assert s["max_dd"] == pytest.approx(-0.1) and s["worst_month"] == pytest.approx(-0.1)
    mean = sum(r) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 2)
    assert s["sharpe"] == pytest.approx(mean / sd * math.sqrt(12))
    assert math.isnan(s["cagr"])  # under 12 months
    assert s["up_share"] == pytest.approx(2 / 3)


def test_versus_counts_months_beaten_and_the_gap():
    lv = scorecard.levels(ledger([100, 110, 99, 120], [1, 1.05, 1.0, 1.1], [1] * 4))
    v = scorecard.versus(lv[scorecard.PAPER], lv[scorecard.EW])
    assert v["months"] == 3 and v["beat"] == 2  # behind only in November
    assert v["excess"] == pytest.approx(0.2 - 0.1)


def test_checks_flag_booking_notes_and_the_ew_race():
    led = ledger([100, 110, 99, 95], [1, 1.05, 1.0, 1.0], [1] * 4, notes={2: ["X: no close"]})
    rows = {c: (s, d) for c, s, d in scorecard.checks(led, scorecard.levels(led))}
    assert rows["Months held"][0] == "3 of 12"
    assert rows["1. Booked from the data, no hand fixes"][0] == "review notes"
    assert rows["2. Beat mid-cap EW after costs"][0] == "behind"


def test_render_handles_an_empty_and_a_booked_ledger(tmp_path):
    assert "No month booked yet" in scorecard.render(momentum.new_ledger())
    page = scorecard.render(ledger([100, 110, 99, 120], [1, 1.05, 1.0, 1.1], [50, 55, 60, 66]))
    assert "<svg" in page and "October 2027 review" in page and "mostly noise" in page
    assert "+10.0 pts" in page  # vs mid-cap EW
    out = tmp_path / "s.html"
    assert momentum.main(["scorecard", "--ledger", str(tmp_path / "none.json"), "--out", str(out)]) == 0
    assert "No month booked yet" in out.read_text()
