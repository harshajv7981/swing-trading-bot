"""FINSABER checks on the TradingAgents calls: random calls and the market split."""

import pytest

from swingbot import agents_trial as at
from swingbot.agent_checks import BAND, by_market, random_picks_p, random_sign_p


def test_calls_that_are_always_right_beat_random_signs_and_wrong_ones_do_not():
    alphas = [0.02, -0.03, 0.01, -0.02, 0.04, -0.01, 0.03, -0.02, 0.02, -0.04]
    right = [1 if a > 0 else -1 for a in alphas]
    assert random_sign_p(alphas, right, draws=2000) < 0.01
    assert random_sign_p(alphas, [-s for s in right], draws=2000) > 0.99


def test_picking_the_best_stock_each_week_beats_random_picks_and_picking_all_ties():
    weeks = [([0.05, 0.0, -0.01, -0.02], 1, 0.05)] * 8
    assert random_picks_p(weeks, draws=2000) < 0.01
    everything = [([0.05, 0.0, -0.01, -0.02], 4, 0.005)] * 8
    assert random_picks_p(everything, draws=200) == pytest.approx(1.0)


def test_market_split_uses_the_flat_band():
    split = by_market([(0.01, BAND * 2), (0.02, 0.0), (-0.01, -BAND * 2), (0.03, BAND)])
    assert split == {"rising": [0.01], "flat": [0.02, 0.03], "falling": [-0.01]}


def test_mid_cap_report_shows_the_index_test_random_check_and_split():
    weeks = [{"date": "2026-11-06", "longs": ["A"], "rets": [0.10, -0.10], "gross": 0.10, "net": 0.098,
              "ew": 0.0, "index": 0.02, "decided": 2, "review": 0}]
    text = at.report({"weeks": weeks, "calls": []}, [{"date": "2026-11-06", "rating": "Buy"}])
    assert "Long calls vs the Nifty Midcap 150, week by week: 1 weeks, mean +8.00%." in text
    assert "Random check: random picks of the same size" in text
    assert "rising 1, mean +10.00%; flat 0; falling 0." in text
