"""Score persona views that were written to a file instead of produced live.

This is how the swarm runs without an API key: Claude Code reads the news
briefs, role-plays each persona itself (see .claude/skills/swarm-backtest/),
and appends one JSON line per persona per round to a views file:

    {"event_id": "e01", "persona": "Retail Bull", "round": 1,
     "stance": 0.7, "conviction": 0.6, "rationale": "..."}

RecordedSwarm takes the last round recorded for each event and hands those
views to the same ReportAgent the live engines use, so every engine is scored
by identical rules.

After the persona rounds, the skill can add a Bull/Bear debate and a judge,
following the researcher and research-manager roles in TradingAgents
(github.com/TauricResearch/TradingAgents, Apache 2.0):

    {"event_id": "e01", "role": "bull", "round": 1, "argument": "..."}
    {"event_id": "e01", "role": "bear", "round": 1, "argument": "..."}
    {"event_id": "e01", "role": "judge", "stance": 0.3, "conviction": 0.5, "rationale": "..."}

Analyst reports written before the personas are kept for the record and not
scored:

    {"event_id": "e01", "role": "analyst", "analyst": "market", "report": "..."}

When an event has a judge line, the judge's call is the signal and the crowd is
kept for the record. `crowd_only()` ignores the debate, so one views file can
score both ways.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..models import NewsEvent, Sentiment, Signal
from .base import SwarmEngine
from .persona_swarm import ReportAgent
from .personas import DEFAULT_PERSONAS, Persona
from .reasoners import PersonaView


DEBATE_PROMPT = """## Bull/Bear debate, then a judge

After the persona rounds for an event, argue it out before deciding:

- Bull researcher: the strongest case that the stock rises over 48 to 72 hours,
  using the persona views and anything else in this brief. Rebut the bear.
- Bear researcher: the strongest case that it falls, rebutting the bull.
- Judge: weigh both cases, side with the stronger argument rather than splitting
  the difference, and give a stance (-1.0 to 1.0), a conviction (0.0 to 1.0) and
  a one or two sentence rationale. Pick a neutral stance near 0 only when the
  arguments truly cancel out.

Every claim must come from this brief. Do not invent numbers."""

ANALYST_PROMPT = """## Analyst reports, before the personas

Each event above comes with data. Before any persona speaks, write up to three
short analyst reports per event, the way TradingAgents' analysts do, and have
the personas read the reports rather than the raw data:

- Market analyst: what the technicals say about trend, momentum and volatility
  going into the news. Quote numbers only from the snapshot.
- News analyst: what the week's earlier news and filings add to this story.
- Fundamentals analyst: what the latest results filing says, if there is one.

Where a section says NO_DATA, WITHHELD or DATA_UNAVAILABLE, the report says the
data is missing. Never fill a gap with an estimate or with what you remember."""

ANALYSTS = ("market", "news", "fundamentals")
DEBATE_ROLES = ("bull", "bear")


@dataclass(frozen=True)
class Debate:
    arguments: list[tuple[int, str, str]]  # (round, role, argument), in file order
    judge: PersonaView | None


class MissingViews(LookupError):
    """No views were recorded for an event."""


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class RecordedSwarm(SwarmEngine):
    name = "recorded"

    def __init__(
        self,
        path: str | Path,
        personas: tuple[Persona, ...] = DEFAULT_PERSONAS,
        horizon: str = "72h",
        report_agent: ReportAgent | None = None,
    ):
        self.path = Path(path)
        self.personas = personas
        self.horizon = horizon
        self.report_agent = report_agent or ReportAgent()
        self.use_debate = True
        self.debates: dict[str, Debate] = {}
        self.reports: dict[str, dict[str, str]] = defaultdict(dict)  # event -> analyst -> report
        self.views = self._load()

    def crowd_only(self) -> None:
        """Score the persona crowd and ignore any Bull/Bear judge."""
        self.use_debate = False
        self.name = "recorded-crowd"

    def _load(self) -> dict[str, list[PersonaView]]:
        known = {p.name for p in self.personas}
        by_event: dict[str, dict[int, dict[str, PersonaView]]] = defaultdict(lambda: defaultdict(dict))
        arguments: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
        judges: dict[str, dict[int, PersonaView]] = defaultdict(dict)
        for n, line in enumerate(self.path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                role = r.get("role")
                if role == "analyst":
                    if r.get("analyst") not in ANALYSTS:
                        raise ValueError(f"unknown analyst {r.get('analyst')!r} (use {', '.join(ANALYSTS)})")
                    self.reports[str(r["event_id"])][r["analyst"]] = str(r["report"])
                    continue
                if role in DEBATE_ROLES:
                    arguments[str(r["event_id"])].append((int(r.get("round", 1)), role, str(r["argument"])))
                    continue
                if role == "judge":
                    judges[str(r["event_id"])][int(r.get("round", 1))] = PersonaView(
                        persona="Judge",
                        stance=_clip(float(r["stance"]), -1.0, 1.0),
                        conviction=_clip(float(r["conviction"]), 0.0, 1.0),
                        rationale=str(r.get("rationale", "")),
                    )
                    continue
                if role is not None:
                    raise ValueError(f"unknown role {role!r} (use analyst, bull, bear or judge)")
                if r["persona"] not in known:
                    raise ValueError(f"unknown persona {r['persona']!r}")
                view = PersonaView(
                    persona=r["persona"],
                    stance=_clip(float(r["stance"]), -1.0, 1.0),
                    conviction=_clip(float(r["conviction"]), 0.0, 1.0),
                    rationale=str(r.get("rationale", "")),
                )
            except (KeyError, ValueError, json.JSONDecodeError) as e:
                raise ValueError(f"{self.path}:{n}: bad view line ({e})") from e
            # A later line for the same event/round/persona replaces an earlier one.
            by_event[str(r["event_id"])][int(r.get("round", 1))][view.persona] = view

        for eid in set(arguments) | set(judges):
            rounds = judges.get(eid, {})
            self.debates[eid] = Debate(arguments.get(eid, []), rounds[max(rounds)] if rounds else None)
        return {
            eid: list(rounds[max(rounds)].values())
            for eid, rounds in by_event.items()
        }

    def simulate(self, event: NewsEvent) -> Signal:
        views = self.views.get(event.event_id)
        if not views:
            raise MissingViews(f"no recorded views for {event.event_id}")
        weights = {p.name: p.weight for p in self.personas}
        crowd = self.report_agent.summarise(event, views, weights, self.horizon)
        debate = self.debates.get(event.event_id)
        if not self.use_debate or debate is None or debate.judge is None:
            return crowd
        return self._judged(crowd, debate.judge)

    def _judged(self, crowd: Signal, judge: PersonaView) -> Signal:
        band = self.report_agent.neutral_band
        if judge.stance > band:
            sentiment = Sentiment.BULLISH
        elif judge.stance < -band:
            sentiment = Sentiment.BEARISH
        else:
            sentiment = Sentiment.NEUTRAL
        confidence = min(judge.conviction, 0.5) if sentiment is Sentiment.NEUTRAL else judge.conviction
        return Signal(
            ticker=crowd.ticker,
            sentiment=sentiment,
            confidence=round(confidence, 4),
            time_horizon=crowd.time_horizon,
            score=round(judge.stance, 4),
            rationale=f"judge {judge.stance:+.2f} after Bull/Bear debate (crowd {crowd.score:+.2f}): {judge.rationale}",
            persona_views=crowd.persona_views,
        )
