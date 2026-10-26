import json
from datetime import datetime

import pytest

from swingbot.backtest import run_backtest
from swingbot.cli import briefs_main
from swingbot.models import IST, NewsEvent, Sentiment
from swingbot.swarm import RecordedSwarm
from swingbot.swarm.recorded import MissingViews

from test_backtest import DictPrices, bars

PERSONAS = ["Retail Bull", "Institutional Bear", "Foreign Institutional Investor", "Financial Media"]


def ev(eid="e1"):
    return NewsEvent(eid, "UP", datetime(2026, 1, 5, 8, tzinfo=IST), "headline")


def write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_uses_last_round(tmp_path):
    f = tmp_path / "v.jsonl"
    write(f, [{"event_id": "e1", "persona": p, "round": 1, "stance": -0.8, "conviction": 0.9} for p in PERSONAS]
          + [{"event_id": "e1", "persona": p, "round": 2, "stance": 0.6, "conviction": 0.7} for p in PERSONAS])
    sig = RecordedSwarm(f).simulate(ev())
    assert sig.sentiment is Sentiment.BULLISH
    assert all(v["stance"] == 0.6 for v in sig.persona_views)


def test_missing_event_is_skipped_not_fatal(tmp_path):
    f = tmp_path / "v.jsonl"
    write(f, [{"event_id": "e1", "persona": p, "stance": 0.5, "conviction": 0.5} for p in PERSONAS])
    res = run_backtest([ev("e1"), ev("e2")], RecordedSwarm(f), DictPrices({"UP": bars([100, 101, 102, 103])}))
    r = res.set_index("event_id")
    assert r.loc["e1", "status"] == "ok"
    assert r.loc["e2", "status"].startswith("no_signal")
    with pytest.raises(MissingViews):
        RecordedSwarm(f).simulate(ev("e2"))


def test_bad_lines_are_rejected(tmp_path):
    f = tmp_path / "v.jsonl"
    write(f, [{"event_id": "e1", "persona": "Random Guy", "stance": 0.5, "conviction": 0.5}])
    with pytest.raises(ValueError, match="unknown persona"):
        RecordedSwarm(f)


def test_briefs_skip_done_events_and_hold_no_prices(tmp_path):
    events = tmp_path / "events.csv"
    events.write_text("event_id,ticker,published_at,headline\n"
                      "e1,UP,2026-01-05 08:00,First story\n"
                      "e2,UP,2026-01-06 08:00,Second story\n")
    views = tmp_path / "v.jsonl"
    write(views, [{"event_id": "e1", "persona": p, "stance": 0.1, "conviction": 0.5} for p in PERSONAS])
    out = tmp_path / "brief.md"
    briefs_main(["--events", str(events), "--views", str(views), "--out", str(out), "--cutoff", "none"])
    text = out.read_text()
    assert "Second story" in text and "First story" not in text
    assert "close" not in text.lower() and "price" not in text.lower()


def test_briefs_drop_news_before_training_cutoff(tmp_path):
    events = tmp_path / "events.csv"
    events.write_text("event_id,ticker,published_at,headline,source\n"
                      "old,UP,2026-03-02 08:00,Old story,gdelt:x\n"
                      "fake,UP,2026-03-02 08:00,Made up story,synthetic\n"
                      "new,UP,2026-08-03 08:00,New story,gdelt:x\n")
    out = tmp_path / "brief.md"
    briefs_main(["--events", str(events), "--out", str(out)])
    text = out.read_text()
    assert "New story" in text and "Made up story" in text and "Old story" not in text
