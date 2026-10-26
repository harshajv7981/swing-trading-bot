import json
from datetime import datetime

import pytest

from swingbot.models import IST, NewsEvent, Sentiment
from swingbot.swarm import RecordedSwarm

PERSONAS = ["Retail Bull", "Institutional Bear", "Foreign Institutional Investor", "Financial Media"]


def ev(eid="e1"):
    return NewsEvent(eid, "UP", datetime(2026, 1, 5, 8, tzinfo=IST), "headline")


def write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def debate(eid="e1", stance=-0.6, conviction=0.8):
    return [
        {"event_id": eid, "role": "bull", "argument": "Order book is huge."},
        {"event_id": eid, "role": "bear", "argument": "Margins were already priced in."},
        {"event_id": eid, "role": "judge", "stance": stance, "conviction": conviction, "rationale": "Bear wins."},
    ]


def test_judge_decides_when_present_and_crowd_only_ignores_it(tmp_path):
    f = tmp_path / "v.jsonl"
    write(f, [{"event_id": "e1", "persona": p, "stance": 0.6, "conviction": 0.7} for p in PERSONAS] + debate())
    sig = RecordedSwarm(f).simulate(ev())
    assert sig.sentiment is Sentiment.BEARISH and sig.confidence == 0.8 and sig.score == -0.6
    assert "Bull/Bear" in sig.rationale and len(sig.persona_views) == 4

    crowd = RecordedSwarm(f)
    crowd.crowd_only()
    assert crowd.simulate(ev()).sentiment is Sentiment.BULLISH
    assert crowd.name == "recorded-crowd"


def test_neutral_judge_caps_confidence_and_bad_roles_fail(tmp_path):
    f = tmp_path / "v.jsonl"
    write(f, [{"event_id": "e1", "persona": p, "stance": 0.6, "conviction": 0.7} for p in PERSONAS]
          + debate(stance=0.05, conviction=0.9))
    sig = RecordedSwarm(f).simulate(ev())
    assert sig.sentiment is Sentiment.NEUTRAL and sig.confidence == 0.5

    write(f, [{"event_id": "e1", "role": "referee", "stance": 0, "conviction": 0}])
    with pytest.raises(ValueError, match="unknown role"):
        RecordedSwarm(f)
