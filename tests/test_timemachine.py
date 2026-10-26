import json

import numpy as np
import pandas as pd
import pytest

from swingbot.risk.cli import main as risk_main
from swingbot.risk.exits import ExitPlan, plan_exit
from swingbot.risk.timemachine import NotEnoughHistory, Odds, base_rate, run, verdict

from test_risk import trend


def flat_days(n, open_=1.0, high=1.01, low=0.99, close=1.0, start="2025-01-01", base=100.0):
    """Every session has the same ratios to the previous close."""
    idx = pd.bdate_range(start, periods=n)
    closes = base * np.cumprod(np.full(n, close))
    prev = np.concatenate([[base], closes[:-1]])
    return pd.DataFrame({"open": prev * open_, "high": prev * high, "low": prev * low,
                         "close": closes, "volume": 1e6}, index=idx)


def plan_for(b, entry=100.0, stop=95.0, target=110.0, atr=2.0):
    return ExitPlan("X", f"{b.index[-1]:%Y-%m-%d}", entry, stop, "price", target, "price", atr)


def test_same_seed_same_numbers_and_seed_matters():
    b = trend()
    plan = plan_exit("X", b, stop_rule="atr:2", target_rule="2R")
    a, again = run(plan, b, replays=2000), run(plan, b, replays=2000)
    assert a.replays == again.replays and a.close_pct == again.close_pct
    assert run(plan, b, replays=2000, seed=20).replays != a.replays
    o = a.replays
    assert o.target_first + o.stop_first + o.neither == pytest.approx(1.0)


def test_steady_rise_always_hits_the_target():
    b = flat_days(200, high=1.03, low=1.0, close=1.02)
    report = run(plan_for(b), b, hold=10, replays=500)
    assert report.replays.target_first == 1.0 and report.replays.avg_r == pytest.approx(2.0)


def test_a_session_touching_both_counts_as_the_stop():
    b = flat_days(200, high=1.2, low=0.9)
    assert run(plan_for(b), b, hold=5, replays=200).replays.stop_first == 1.0


def test_a_gap_through_the_stop_fills_at_the_open_and_costs_more_than_1r():
    b = flat_days(200, open_=0.90, high=0.91, low=0.89, close=0.90)
    o = run(plan_for(b), b, hold=5, replays=200).replays
    assert o.gap_through_stop == 1.0
    assert o.avg_r == pytest.approx(-2.0)  # open at 90 with entry 100 and a 5-point stop


def test_neither_ends_at_the_last_close():
    b = flat_days(200, high=1.001, low=0.999, close=1.0)
    o = run(plan_for(b), b, hold=5, replays=100).replays
    assert o.neither == 1.0 and o.avg_r == pytest.approx(0.0)


def test_future_bars_never_change_the_result():
    b = trend()
    plan = plan_exit("X", b.iloc[:-30], stop_rule="atr:2", target_rule="2R")
    shocked = b.copy()
    shocked.iloc[-30:, :4] *= 3
    assert run(plan, b, replays=1000).to_json() == run(plan, shocked, replays=1000).to_json()


def test_refuses_without_enough_history():
    b = trend(n=80)
    with pytest.raises(NotEnoughHistory):
        run(plan_exit("X", b), b)


def test_base_rate_walks_the_real_bars():
    b = flat_days(300, high=1.03, low=1.0, close=1.02)
    plan = plan_for(b, stop=95.0, target=110.0, atr=2.5)  # 2 ATR stop, 2R target
    o = base_rate(plan, b, hold=10)
    assert o is not None and o.trades > 200 and o.target_first == 1.0


@pytest.mark.parametrize("target, stop, rr, words", [
    (0.50, 0.50, 1.0, "close to a coin flip"),
    (0.40, 0.60, 2.0, "leans toward the target"),
    (0.10, 0.90, 2.0, "strongly toward the stop"),
    (0.33, 0.67, 2.0, "close to break-even"),
])
def test_verdict_compares_with_break_even(target, stop, rr, words):
    text, _ = verdict(Odds(1000, target, stop, 0.0, 0.0, 0.0), rr)
    assert text.startswith(words)


def test_verdict_notes_many_open_replays_and_gaps():
    _, notes = verdict(Odds(1000, 0.2, 0.3, 0.5, 0.05, 0.1), 1.0)
    assert any("hit neither" in n for n in notes) and any("gapped through" in n for n in notes)


def test_cli_odds_writes_a_report(tmp_path, capsys):
    prices = tmp_path / "prices"
    prices.mkdir()
    b = trend()
    b.to_csv(prices / "RELIANCE.csv", index_label="date")
    data_cfg = tmp_path / "data.toml"
    data_cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    plan = plan_exit("RELIANCE", b, stop_rule="atr:2", target_rule="2R")
    (tmp_path / "plan.json").write_text(json.dumps(plan.to_json()))
    out = tmp_path / "odds.json"
    assert risk_main(["--data-config", str(data_cfg), "odds", str(tmp_path / "plan.json"),
                      "--replays", "500", "--out", str(out)]) == 0
    assert "Verdict:" in capsys.readouterr().out
    assert json.loads(out.read_text())["seed"] == 19
