import json

import pandas as pd
import pytest

from swingbot.metrics import benjamini_hochberg, bootstrap_ci
from swingbot.scorecard import FINAL, JUDGE, calls, main as scorecard_main, scorecard

PERSONAS = ["Retail Bull", "Institutional Bear", "Foreign Institutional Investor", "Financial Media"]


def test_benjamini_hochberg_matches_the_textbook():
    # Worked example: p = .01 .04 .03 .005 -> q = .02 .04 .04 .02
    assert benjamini_hochberg([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert benjamini_hochberg([None, 0.5]) == [None, 0.5]


def test_bootstrap_ci_brackets_the_mean_and_repeats():
    vals = [0.01, -0.02, 0.03, 0.02, 0.0, 0.015]
    lo, hi = bootstrap_ci(vals)
    assert lo < sum(vals) / len(vals) < hi
    assert bootstrap_ci(vals) == (lo, hi)
    assert bootstrap_ci([0.1]) is None


def fixture(tmp_path, n=12):
    """n events, all up 2%. The bull is always long, the bear always short, the media sits out."""
    results = pd.DataFrame([{
        "event_id": f"e{i}", "ticker": "ABC", "status": "ok", "entry_date": f"2026-0{1 + i % 2}-05",
        "ret": 0.02, "sentiment": "bullish", "score": 0.4, "confidence": 0.6,
    } for i in range(n)])
    lines = []
    for i in range(n):
        for p, stance in zip(PERSONAS, [0.8, -0.6, 0.3, 0.05]):
            lines.append({"event_id": f"e{i}", "persona": p, "round": 2, "stance": stance, "conviction": 0.7})
        lines.append({"event_id": f"e{i}", "role": "judge", "stance": 0.5, "conviction": 0.6, "rationale": ""})
    views = tmp_path / "views.jsonl"
    views.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    results_path = tmp_path / "results.csv"
    results.to_csv(results_path, index=False)
    return results, views, results_path


def test_each_persona_is_scored_on_its_own_calls(tmp_path):
    results, views, _ = fixture(tmp_path)
    card = scorecard(calls(results, views)).set_index("who")
    assert card.loc["Retail Bull", "hit_rate"] == 1.0
    assert card.loc["Institutional Bear", "hit_rate"] == 0.0
    assert card.loc["Financial Media", "calls"] == 0 and card.loc["Financial Media", "no_call"] == 12
    assert card.loc[JUDGE, "calls"] == 12 and card.loc[FINAL, "calls"] == 12
    assert card.loc["Retail Bull", "avg_trade_ret"] == pytest.approx(0.02 - 0.0025)
    # 12 for 12 is p = 1/4096; corrected for the rows tested it stays tiny.
    assert card.loc["Retail Bull", "p_value"] == pytest.approx(1 / 4096)
    assert card.loc["Retail Bull", "q_value"] < 0.01
    assert list(card.index[:2]) == [FINAL, JUDGE]


def test_short_calls_are_graded_but_earn_nothing(tmp_path):
    # The bot is long-only: the bear's calls count for direction, never as money made.
    results, views, _ = fixture(tmp_path)
    card = scorecard(calls(results, views)).set_index("who")
    bear = card.loc["Institutional Bear"]
    assert bear["calls"] == 12 and bear["long_calls"] == 0
    assert bear["avg_trade_ret"] is None or pd.isna(bear["avg_trade_ret"])
    assert card.loc["Retail Bull", "long_calls"] == 12


def test_by_month_splits_rows(tmp_path):
    results, views, _ = fixture(tmp_path)
    card = scorecard(calls(results, views), by="month")
    assert set(card["month"]) == {"2026-01", "2026-02"}
    assert card[card["who"] == "Retail Bull"]["calls"].tolist() == [6, 6]


def test_cli(tmp_path, capsys):
    _, views, results_path = fixture(tmp_path)
    out = tmp_path / "card.csv"
    assert scorecard_main(["--run", str(results_path), str(views), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "Retail Bull" in text and "beats luck" in text
    assert len(pd.read_csv(out)) == 6
