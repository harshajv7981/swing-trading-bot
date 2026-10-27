import json
from datetime import datetime

import pandas as pd
import pytest

from swingbot.cli import briefs_main
from swingbot.memory import ClosedTrade, Lesson, MemoryStore, from_results, similar_lessons
from swingbot.memory.cli import main as memory_main
from swingbot.memory.compare import compare
from swingbot.models import IST, NewsEvent


def ts(s):
    return datetime.fromisoformat(s).replace(tzinfo=IST)


def trade(eid, published, closed, headline="DemoInfra wins highway contract", ticker="DEMOINFRA", hit=False, run="base"):
    return ClosedTrade(run=run, event_id=eid, ticker=ticker, published_at=ts(published), headline=headline,
                       sentiment="bullish", closed_at=ts(closed), confidence=0.7, ret=-0.03, hit=hit)


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    yield s
    s.close()


def test_lessons_only_from_trades_closed_before_the_news(store):
    store.record_trade(trade("old", "2026-07-01 08:00", "2026-07-03 15:30"))
    store.record_trade(trade("open", "2026-07-06 08:00", "2026-07-08 15:30"))
    for eid in ("old", "open"):
        store.add_lesson(Lesson(f"base:{eid}", "wrong", "Highway contract wins faded fast.", ("order win",)))

    event = NewsEvent("new", "DEMOINFRA", ts("2026-07-07 10:00"), "DemoInfra bags another highway contract")
    seen = [h["event_id"] for _, h in similar_lessons(store, event)]
    assert seen == ["old"]  # "open" was still running when this story broke

    # Exactly at the close is still too early: strictly before.
    at_close = NewsEvent("n2", "DEMOINFRA", ts("2026-07-08 15:30"), "DemoInfra highway contract")
    assert [h["event_id"] for _, h in similar_lessons(store, at_close)] == ["old"]


def test_similarity_prefers_matching_news(store):
    store.record_trade(trade("a", "2026-07-01 08:00", "2026-07-03 15:30", "DemoBank profit misses as bad loans rise", "DEMOBANK"))
    store.record_trade(trade("b", "2026-07-01 08:00", "2026-07-03 15:30", "DemoPharma gets FDA approval", "DEMOPHARMA"))
    store.add_lesson(Lesson("base:a", "wrong", "Bad loan surprises keep weighing for days.", ("banks", "asset quality")))
    store.add_lesson(Lesson("base:b", "right_for_right_reason", "FDA approvals pop on day one.", ("pharma",)))
    event = NewsEvent("q", "OTHERBANK", ts("2026-07-10 08:00"), "OtherBank bad loans rise, profit misses")
    hits = similar_lessons(store, event)
    assert hits[0][1]["event_id"] == "a"
    assert all(h["event_id"] != "b" for _, h in hits)  # unrelated story falls below the floor


def test_from_results_closes_at_exit_session_close():
    df = pd.DataFrame([
        {"event_id": "e1", "ticker": "X", "published_at": "2026-07-01T08:00:00+05:30", "headline": "h",
         "status": "ok", "sentiment": "bullish", "confidence": 0.6, "score": 0.4, "rationale": "r",
         "entry_date": "2026-07-01", "exit_date": "2026-07-03", "ret": 0.02, "trade_ret": 0.0175,
         "bench_ret": 0.01, "max_up": 0.03, "max_down": -0.01, "hit": True},
        {"event_id": "e2", "ticker": "X", "published_at": "2026-07-02T08:00:00+05:30", "headline": "h",
         "status": "no_signal: none"},
    ])
    [t] = from_results(df, "base")
    assert t.closed_at == ts("2026-07-03 15:30")
    assert t.hit is True and t.trade_id == "base:e1"


def test_trade_cannot_close_before_news():
    with pytest.raises(ValueError, match="closed before"):
        trade("x", "2026-07-05 08:00", "2026-07-03 15:30")


def test_lesson_needs_known_trade_and_verdict(store):
    with pytest.raises(KeyError):
        store.add_lesson(Lesson("base:nope", "wrong", "x"))
    with pytest.raises(ValueError, match="verdict"):
        Lesson("base:x", "meh", "x")


def test_briefs_with_lessons_and_audit(tmp_path, store):
    store.record_trade(trade("old", "2026-07-01 08:00", "2026-07-03 15:30"))
    store.add_lesson(Lesson("base:old", "wrong", "Highway contract wins faded fast.", ("order win",)))
    store.close()
    events = tmp_path / "events.csv"
    events.write_text("event_id,ticker,published_at,headline\n"
                      "e1,DEMOINFRA,2026-07-02 08:00,DemoInfra wins highway contract\n"
                      "e2,DEMOINFRA,2026-07-10 08:00,DemoInfra wins highway contract again\n"
                      "e3,DEMOINFRA,2026-07-20 08:00,DemoInfra highway contract\n")
    out = tmp_path / "brief.md"
    db = str(tmp_path / "m.db")
    briefs_main(["--events", str(events), "--out", str(out), "--with-lessons", db, "--until", "2026-07-15"])
    text = out.read_text()
    assert "e3" not in text  # outside the window
    e1, e2 = text.split("### e2")
    assert "faded fast" not in e1  # lesson's trade hadn't closed on 2 July
    assert "faded fast" in e2
    assert memory_main(["--db", db, "audit"]) == 0

    s = MemoryStore(db)
    s.db.execute("INSERT INTO retrievals VALUES ('e9', '2026-07-02T08:00:00+05:30', 'base:old', 1, 'now')")
    s.db.commit()
    assert len(s.audit()) == 1
    s.close()


def test_ingest_review_learn_roundtrip(tmp_path):
    res = tmp_path / "base" / "results.csv"
    res.parent.mkdir()
    pd.DataFrame([{"event_id": "e1", "ticker": "X", "published_at": "2026-07-01T08:00:00+05:30",
                   "headline": "X wins order", "status": "ok", "sentiment": "bullish", "confidence": 0.6,
                   "score": 0.4, "rationale": "judge said so", "entry_date": "2026-07-01",
                   "exit_date": "2026-07-03", "ret": -0.02, "trade_ret": -0.0225, "hit": False}]).to_csv(res, index=False)
    views = tmp_path / "views.jsonl"
    views.write_text(json.dumps({"event_id": "e1", "persona": "Retail Bull", "round": 2, "stance": 0.8,
                                 "conviction": 0.7, "rationale": "Moon!"}) + "\n")
    db = str(tmp_path / "m.db")
    assert memory_main(["--db", db, "ingest", "--results", str(res), "--views", str(views)]) == 0
    brief = tmp_path / "verify.md"
    memory_main(["--db", db, "review", "--out", str(brief), "--closed-before", "2026-07-04"])
    text = brief.read_text()
    assert "base:e1" in text and "Moon!" in text and "wrong" in text
    memory_main(["--db", db, "review", "--out", str(brief), "--closed-before", "2026-07-03"])
    assert "base:e1" not in brief.read_text()

    lessons = tmp_path / "lessons.jsonl"
    lessons.write_text(json.dumps({"trade_id": "base:e1", "verdict": "wrong", "lesson": "Order wins fade.",
                                   "tags": ["order win"]}) + "\n")
    memory_main(["--db", db, "learn", "--lessons", str(lessons)])
    s = MemoryStore(db)
    assert s.counts()["lessons"] == 1 and not s.trades(without_lesson=True)
    s.close()


def test_compare_pairs_events():
    base = {"status": "ok", "confidence": 0.6, "score": 0.5, "ret": 0.02}
    without = pd.DataFrame([{**base, "event_id": "a", "sentiment": "bullish", "hit": True, "trade_ret": 0.0175},
                            {**base, "event_id": "b", "sentiment": "bullish", "hit": False, "trade_ret": -0.03},
                            {**base, "event_id": "c", "sentiment": "bullish", "hit": True, "trade_ret": 0.01}])
    with_ = pd.DataFrame([{**base, "event_id": "a", "sentiment": "bullish", "hit": True, "trade_ret": 0.0175},
                          {**base, "event_id": "b", "sentiment": "neutral", "hit": None, "trade_ret": 0.0},
                          {"event_id": "z", "status": "ok", "sentiment": "bullish", "hit": True, "trade_ret": 0.5,
                           "confidence": 0.5, "score": 0.1, "ret": 0.5}])
    c = compare(without, with_)
    assert c["events"] == 2  # only a and b were scored by both
    assert c["calls_changed"] == 1 and c["memory_better"] == 1 and c["memory_worse"] == 0


def test_verifier_brief_says_what_the_long_only_bot_did():
    from swingbot.memory.verifier import review_brief

    base = {"event_id": "e1", "ticker": "X", "published_at": "2026-07-01T08:00:00+05:30", "headline": "h",
            "status": "ok", "confidence": 0.6, "score": 0.4, "rationale": "r", "entry_date": "2026-07-01",
            "exit_date": "2026-07-03", "ret": -0.02, "bench_ret": 0.0, "max_up": 0.01, "max_down": -0.03}
    df = pd.DataFrame([
        {**base, "sentiment": "bearish", "hit": True, "trade_ret": 0.0, "traded": False,
         "no_trade_reason": "bearish: long-only, no short"},
        {**base, "event_id": "e2", "sentiment": "bullish", "hit": False, "trade_ret": -0.0425, "traded": True,
         "trade_entry": 100.0, "trade_stop": 96.0, "trade_target": 108.0, "trade_exit_date": "2026-07-02",
         "trade_exit_price": 96.0, "exit_reason": "stop", "trade_r": -1.0},
    ])
    short, long_ = from_results(df, "base")
    assert short.extra["trade"] == {"traded": False, "no_trade_reason": "bearish: long-only, no short"}
    rows = [{**t.__dict__, "trade_id": t.trade_id, "extra": json.dumps(t.extra)} for t in (short, long_)]
    brief = review_brief(rows)
    assert "Call was right. No trade (bearish: long-only, no short)." in brief
    assert "Bot bought at 100.00, stop 96.00, target 108.00; out on 2026-07-02 at 96.00 (stop)" in brief
