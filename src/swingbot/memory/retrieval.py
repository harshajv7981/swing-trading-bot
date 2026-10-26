"""Find past lessons that look like the news in front of the swarm.

Similarity is TF-IDF cosine over the headline, the lesson and its tags, plus a
bonus for the same stock. Pure Python, no model and no API, so it is free,
deterministic and fast enough for thousands of trades. If it proves too crude,
swap `score_lessons` for local embeddings; the time gate stays in
`MemoryStore.lessons_known_at` either way.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from ..models import NewsEvent
from .store import MemoryStore

STOPWORDS = frozenset(
    "a an and as at be by for from has have in is it its of on or the to was were will with "
    "ltd limited rs crore cr q1 q2 q3 q4 fy says said".split()
)
SAME_TICKER_BONUS = 0.25
MIN_SIMILARITY = 0.08

MEMORY_PROMPT = """Lessons below come from the Verifier's reviews of earlier trades that had already
closed when this story broke. They are advice, not data about this event: weigh
them, and say in a rationale when one changed a view. Ignore any that don't fit."""


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _doc(lesson: dict) -> list[str]:
    return tokens(f"{lesson['headline']} {lesson['lesson']} {' '.join(lesson['tags'])}")


def score_lessons(event: NewsEvent, lessons: list[dict]) -> list[tuple[float, dict]]:
    """(similarity, lesson) pairs, best first, above MIN_SIMILARITY."""
    if not lessons:
        return []
    docs = [Counter(_doc(l)) for l in lessons]
    df = Counter(w for d in docs for w in d)
    n = len(docs)
    idf = {w: math.log((1 + n) / (1 + c)) + 1 for w, c in df.items()}

    def vec(counts: Counter) -> dict[str, float]:
        v = {w: c * idf.get(w, math.log(1 + n) + 1) for w, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / norm for w, x in v.items()}

    q = vec(Counter(tokens(f"{event.headline} {event.body}")))
    scored = []
    for lesson, d in zip(lessons, docs):
        dv = vec(d)
        sim = sum(x * dv.get(w, 0.0) for w, x in q.items())
        if lesson["ticker"] == event.ticker:
            sim += SAME_TICKER_BONUS
        if sim >= MIN_SIMILARITY:
            scored.append((sim, lesson))
    scored.sort(key=lambda p: (-p[0], p[1]["closed_at"]))
    return scored


def similar_lessons(store: MemoryStore, event: NewsEvent, k: int = 3) -> list[tuple[float, dict]]:
    """Up to k lessons from trades that closed before `event` was published.

    One lesson per past event: if two runs traded the same news, the closer
    match wins, so the swarm isn't shown the same story twice.
    """
    best: dict[str, tuple[float, dict]] = {}
    for sim, lesson in score_lessons(event, store.lessons_known_at(event.published_at)):
        if lesson["event_id"] == event.event_id:
            continue  # can't happen under the time gate, but never show an event its own trade
        prev = best.get(lesson["event_id"])
        if prev is None or sim > prev[0]:
            best[lesson["event_id"]] = (sim, lesson)
    return sorted(best.values(), key=lambda p: -p[0])[:k]


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def brief_section(store: MemoryStore, event: NewsEvent, k: int = 3, log: bool = True) -> str:
    """The "Lessons from past trades" block for one event in a swarm brief."""
    hits = similar_lessons(store, event, k)
    if log:
        store.log_retrieval(event.event_id, event.published_at, hits)
    if not hits:
        return "Lessons from past trades: none that closed before this story looks similar."
    lines = ["Lessons from past trades:"]
    for sim, h in hits:
        outcome = "call right" if h["hit"] == 1 else "call wrong" if h["hit"] == 0 else "no call"
        lines.append(
            f"- {h['ticker']} {h['published_at'][:10]}, \"{h['headline']}\": called {h['sentiment']}, "
            f"moved {_pct(h['ret'])} ({outcome}; Verifier: {h['verdict'].replace('_', ' ')}). "
            f"Lesson: {h['lesson']}"
        )
    calls = [h for _, h in hits if h["hit"] is not None]
    if calls:
        lines.append(f"  Similar past calls right: {sum(h['hit'] for h in calls)} of {len(calls)}.")
    return "\n".join(lines)
