"""Score persona views that were written to a file instead of produced live.

This is how the swarm runs without an API key: Claude Code reads the news
briefs, role-plays each persona itself (see .claude/skills/swarm-backtest/),
and appends one JSON line per persona per round to a views file:

    {"event_id": "e01", "persona": "Retail Bull", "round": 1,
     "stance": 0.7, "conviction": 0.6, "rationale": "..."}

RecordedSwarm takes the last round recorded for each event and hands those
views to the same ReportAgent the live engines use, so every engine is scored
by identical rules.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..models import NewsEvent, Signal
from .base import SwarmEngine
from .persona_swarm import ReportAgent
from .personas import DEFAULT_PERSONAS, Persona
from .reasoners import PersonaView


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
        self.views = self._load()

    def _load(self) -> dict[str, list[PersonaView]]:
        known = {p.name for p in self.personas}
        by_event: dict[str, dict[int, dict[str, PersonaView]]] = defaultdict(lambda: defaultdict(dict))
        for n, line in enumerate(self.path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
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

        return {
            eid: list(rounds[max(rounds)].values())
            for eid, rounds in by_event.items()
        }

    def simulate(self, event: NewsEvent) -> Signal:
        views = self.views.get(event.event_id)
        if not views:
            raise MissingViews(f"no recorded views for {event.event_id}")
        weights = {p.name: p.weight for p in self.personas}
        return self.report_agent.summarise(event, views, weights, self.horizon)
