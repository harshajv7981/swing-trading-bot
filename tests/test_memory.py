"""Step 7: episode records, the mistake taxonomy and market-scoped memory
(docs/parallel-markets-learning-plan.md, "Memory and learning design")."""

import json

import pytest

from swingbot.markets.memory import Memory, MemoryRefused, Mistake, classify, show
from swingbot.markets.trials import TrialMonth


def month(market, m, ret, since, bench_since, anomalies=(), exposure=0.95, orders=(20, 20, 0)):
    main = "Mid-cap EW" if market == "nse" else "RSP"
    return TrialMonth(market, f"{market}-m", "1", m, f"{m}-28", f"{m}-28T10:00:00Z",
                      "INR" if market == "nse" else "USD", 150, ["A"], orders, 100.0, ret, since,
                      {main: bench_since}, 0.0, exposure, list(anomalies))


def test_the_taxonomy_is_the_plans_ten_categories():
    assert len(Mistake) == 10
    assert Mistake.EXPECTED_LOSS.value == "expected loss with correct process"
    assert Mistake.MARKET_ASSUMPTION_REUSED in Mistake.judgment_only()


def test_episodes_open_settle_and_classify_once(tmp_path):
    mem = Memory(tmp_path)
    rows = [month("us", "2026-11", 0.0, 0.0, 0.0), month("us", "2026-12", -0.02, -0.02, 0.01)]
    assert mem.update("us", rows) == 3                 # Nov opened, Nov settled, Dec opened
    ep = mem.episodes("us")["us-us-m-2026-11"]
    assert ep["realized"] == pytest.approx(-0.02) and ep["bench"] == pytest.approx(0.01)
    assert ep["excess"] == pytest.approx(-0.03)
    assert ep["mistakes"] == [Mistake.EXPECTED_LOSS.value]       # lost, nothing went wrong in the process
    assert mem.episodes("us")["us-us-m-2026-12"]["status"] == "open"
    size = (tmp_path / "markets/us/memory/episodes.jsonl").stat().st_size
    assert mem.update("us", rows) == 0                 # re-running adds nothing
    assert (tmp_path / "markets/us/memory/episodes.jsonl").stat().st_size == size


def test_the_classifier_only_names_what_the_record_proves():
    clean = month("us", "2026-11", 0.0, 0.0, 0.0)
    assert classify(clean, excess=0.01) == ([], [])
    m, ev = classify(month("us", "2026-11", 0.0, 0.0, 0.0,
                           anomalies=["us-m12-2026-11-X-buy was not filled",
                                      "us-m12-2026-11-Y-buy rejected: insufficient buying power",
                                      "Z: Alpaca holds 3, the book's fills say 5"]), excess=-0.01)
    assert set(m) == {Mistake.EXECUTION.value, Mistake.BROKER.value}
    assert Mistake.EXPECTED_LOSS.value not in m and len(ev) == 3
    m, _ = classify(month("nse", "2026-11", 0.0, 0.0, 0.0, exposure=1.2), excess=0.02)
    assert m == [Mistake.RISK.value]


def test_a_reviewer_can_add_a_judgment_with_evidence_only(tmp_path):
    mem = Memory(tmp_path)
    mem.update("nse", [month("nse", "2026-10", 0.0, 0.0, 0.0), month("nse", "2026-11", -0.05, -0.05, 0.02)])
    with pytest.raises(MemoryRefused, match="evidence"):
        mem.classify("nse-nse-m-2026-10", Mistake.REGIME, evidence="", by="harsha")
    mem.classify("nse-nse-m-2026-10", Mistake.REGIME, evidence="mid-caps sold off on a rate shock", by="harsha")
    assert Mistake.REGIME.value in mem.episodes("nse")["nse-nse-m-2026-10"]["mistakes"]


def test_lessons_stay_in_their_market_and_only_a_person_approves(tmp_path):
    mem = Memory(tmp_path)
    mem.update("us", [month("us", "2026-11", 0.0, 0.0, 0.0), month("us", "2026-12", -0.02, -0.02, 0.01)])
    mem.update("nse", [month("nse", "2026-10", 0.0, 0.0, 0.0), month("nse", "2026-11", -0.05, -0.05, 0.02)])
    with pytest.raises(MemoryRefused, match="nse"):
        mem.propose("nse", "market", "Alpaca rejects odd lots", ["us-us-m-2026-11"], "low", by="claude")
    lid = mem.propose("us", "market", "Check buying power before buys", ["us-us-m-2026-11"], "low", by="claude")
    with pytest.raises(MemoryRefused, match="person"):
        mem.decide("us", lid, "approve", by="claude", why="looks right")
    mem.decide("us", lid, "approve", by="harsha", why="seen twice")
    assert mem.lessons("us")[lid]["status"] == "approved"
    assert lid not in mem.lessons("nse")
    with pytest.raises(MemoryRefused, match="separate"):
        mem.propose("shared", "market", "Refuse stale data", ["us-us-m-2026-11", "nse-nse-m-2026-10"],
                    "medium", by="claude")
    nse_lid = mem.propose("nse", "market", "Eod2 data lags a week", ["nse-nse-m-2026-10"], "low", by="claude")
    assert list(mem.lessons("nse")) == [nse_lid] and nse_lid not in mem.lessons("us")
    view = show(mem, "us")
    assert lid in view and nse_lid not in view and "nse-" not in view
    with pytest.raises(MemoryRefused, match="confidence"):
        mem.propose("us", "market", "x", ["us-us-m-2026-11"], "certain", by="claude")


def test_a_repeated_mistake_proposes_a_market_lesson_once(tmp_path):
    mem = Memory(tmp_path)
    bad = ["us-m12-X-buy was not filled"]
    rows = [month("us", "2026-11", 0.0, 0.0, 0.0, anomalies=bad), month("us", "2026-12", 0.0, 0.0, 0.0, anomalies=bad),
            month("us", "2027-01", 0.0, 0.0, 0.0)]
    mem.update("us", rows)
    proposed = [v for v in mem.lessons("us").values() if v["status"] == "proposed"]
    assert len(proposed) == 1 and "execution error" in proposed[0]["text"]
    assert sorted(proposed[0]["evidence"]) == ["us-us-m-2026-11", "us-us-m-2026-12"]
    assert proposed[0]["proposed_by"] == "auto"
    mem.update("us", rows)
    assert len(mem.lessons("us")) == 1
    lines = (tmp_path / "markets/us/memory/lessons.jsonl").read_text().splitlines()
    assert all(json.loads(x)["kind"].startswith("lesson_") for x in lines)
