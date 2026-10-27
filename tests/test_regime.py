import json

import numpy as np
import pandas as pd
import pytest

from swingbot.regime import Mood, assess
from swingbot.risk import Order, RiskConfig, SizingRefused, size_position
from swingbot.risk.cli import main as risk_main
from swingbot.risk.exits import ExitPlan

from test_risk import book, check, entry

AS_OF = pd.Timestamp("2026-10-23 18:00", tz="Asia/Kolkata")  # a Friday, after the close


def series(daily_step, n=260, end="2026-10-23", start_price=100.0):
    idx = pd.bdate_range(end=end, periods=n)
    close = start_price * np.cumprod(np.full(n, 1 + daily_step))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1e6}, index=idx)


def flat(level, n=260, end="2026-10-23"):
    return series(0.0, n, end, level)


def risk_on():
    return {"nifty": series(0.003), "fmcg": series(0.0), "gold": series(-0.001), "usdinr": series(-0.0002),
            "vix": flat(12.0)}


def risk_off():
    return {"nifty": series(-0.003), "fmcg": series(0.001), "gold": series(0.003), "usdinr": series(0.001),
            "vix": flat(24.0)}


def test_risk_on_market_is_bullish():
    mood = assess(risk_on(), AS_OF)
    assert mood.verdict == "bullish"
    assert mood.available == 5 and mood.defensive == 0


def test_risk_off_market_is_defensive():
    mood = assess(risk_off(), AS_OF)
    assert mood.verdict == "defensive"
    assert {c.id: c.status for c in mood.checks}["india_vix"] == "defensive"


def test_missing_series_are_unavailable_not_neutral():
    mood = assess({"nifty": series(0.003), "vix": None}, AS_OF)
    statuses = {c.id: c.status for c in mood.checks}
    assert statuses["india_vix"] == statuses["nifty_vs_fmcg"] == statuses["safe_havens"] == "unavailable"
    assert mood.available == 2 and mood.verdict == "unknown"
    assert "Unknown is not neutral" in mood.text()


def test_short_history_skips_the_200_day_check_only():
    mood = assess({**risk_on(), "nifty": series(0.003, n=60)}, AS_OF)
    statuses = {c.id: c.status for c in mood.checks}
    assert statuses["nifty_long_trend"] == "unavailable"
    assert statuses["nifty_trend"] == "bullish"


def test_never_reads_a_close_after_the_as_of_time():
    # Calm until the 23rd, then VIX spikes on the 26th. Before that close the mood can't know.
    vix = flat(12.0, end="2026-10-26")
    vix.loc[pd.Timestamp("2026-10-26"), "close"] = 40.0
    data = {**risk_on(), "vix": vix}
    noon = pd.Timestamp("2026-10-26 12:00", tz="Asia/Kolkata")
    assert {c.id: c.value for c in assess(data, noon).checks}["india_vix"] == 12.0
    evening = pd.Timestamp("2026-10-26 16:00", tz="Asia/Kolkata")
    assert {c.id: c.value for c in assess(data, evening).checks}["india_vix"] == 40.0


def test_mood_round_trips_through_json():
    mood = assess(risk_off(), AS_OF)
    again = Mood.from_json(json.loads(json.dumps(mood.to_json())))
    assert again.verdict == mood.verdict and again.checks == mood.checks


# --- R12 in sizing and the gate -----------------------------------------------

PLAN = ExitPlan("RELIANCE", "2026-10-23", 1000.0, 960.0, "atr:2", 1080.0, "2R", 20.0)
DEFENSIVE = {"as_of": "2026-10-26 09:00 IST", "verdict": "defensive"}
BULLISH = {"as_of": "2026-10-26 09:00 IST", "verdict": "bullish"}


def test_defensive_mood_halves_the_size():
    full = size_position(PLAN, book(), RiskConfig(), "Energy", mood=BULLISH)
    cut = size_position(PLAN, book(), RiskConfig(), "Energy", mood=DEFENSIVE)
    assert cut.risk_pct == 0.5 and full.risk_pct == 1.0
    assert cut.order.qty < full.order.qty
    assert cut.order.mood["verdict"] == "defensive"


def test_scale_zero_turns_entries_off():
    with pytest.raises(SizingRefused, match="defensive"):
        size_position(PLAN, book(), RiskConfig(defensive_risk_scale=0.0), "Energy", mood=DEFENSIVE)


def test_scale_out_of_range_is_refused():
    with pytest.raises(ValueError):
        RiskConfig(defensive_risk_scale=1.5)


def test_gate_cuts_r2_when_defensive():
    # 140 shares lose 140 x (1000 - 960 x 0.995) = 6,272 at the stop: 0.63% of 10 lakh.
    order, account = entry(qty=140), book(equity=1_000_000)
    assert check(order, account, mood=BULLISH).passed
    v = check(order, account, mood=DEFENSIVE)
    assert "R2 risk per trade" in {c.rule for c in v.checks if not c.ok}
    assert any(c.rule == "R12 market mood" and c.ok for c in v.checks)


def test_gate_uses_the_mood_the_order_was_sized_under():
    order = Order.from_json({**entry(qty=140).to_json(), "mood": DEFENSIVE})
    assert not check(order, book(equity=1_000_000)).passed


def test_gate_warns_when_mood_missing_or_stale():
    v = check(entry(qty=10))
    assert any("market mood not checked" in w for w in v.warnings)
    v = check(entry(qty=10), mood={"as_of": "2026-10-20 09:00 IST", "verdict": "neutral"})
    assert any("run it again" in w for w in v.warnings)


def test_mood_cli_reads_saved_csvs(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    names = {"nifty": "NIFTY50", "fmcg": "NIFTYFMCG", "gold": "GOLDBEES", "usdinr": "USDINR", "vix": "INDIAVIX"}
    for key, df in risk_off().items():
        df.rename_axis("date").to_csv(prices / f"{names[key]}.csv")
    cfg = tmp_path / "data.toml"
    cfg.write_text(f'[sources]\nprices = "csv"\n[paths]\nprices_dir = "{prices}"\n')
    out = tmp_path / "mood.json"
    assert risk_main(["--data-config", str(cfg), "mood", "--as-of", "2026-10-23", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["verdict"] == "defensive"


def test_briefs_show_the_mood_as_of_each_story(tmp_path):
    from swingbot.cli import briefs_main

    prices = tmp_path / "prices"
    prices.mkdir()
    names = {"nifty": "NIFTY50", "fmcg": "NIFTYFMCG", "gold": "GOLDBEES", "usdinr": "USDINR", "vix": "INDIAVIX"}
    for key, df in risk_on().items():
        df.rename_axis("date").to_csv(prices / f"{names[key]}.csv")
    events = tmp_path / "events.csv"
    events.write_text("event_id,ticker,published_at,headline,body,source\n"
                      "e1,ABC,2026-10-20 08:30+05:30,ABC wins an order,,synthetic\n")
    out = tmp_path / "brief.md"
    assert briefs_main(["--events", str(events), "--with-mood", str(prices), "--out", str(out)]) == 0
    text = out.read_text()
    assert "## Market mood" in text
    assert "Market mood as of 2026-10-20 08:30 IST: BULLISH" in text


def test_usdinr_bar_is_known_only_from_next_morning():
    from swingbot.regime import fx_bars_known_at
    bars = flat(84.0, n=5)
    last = bars.index[-1]  # 2026-10-23
    assert fx_bars_known_at(bars, AS_OF).index[-1] < last  # 18:00 the same day: not yet
    later = pd.Timestamp("2026-10-24 07:00", tz="Asia/Kolkata")
    assert fx_bars_known_at(bars, later).index[-1] == last
    # The mood's dollar-rupee check uses the cut, so a jump on the news day is not seen yet.
    s = risk_on()
    s["usdinr"] = s["usdinr"].copy()
    s["usdinr"].iloc[-1, s["usdinr"].columns.get_loc("close")] *= 1.05
    havens = {c.id: c for c in assess(s, AS_OF).checks}["safe_havens"]
    assert havens.status != "defensive"
    havens = {c.id: c for c in assess(s, later).checks}["safe_havens"]
    assert havens.status == "defensive"
