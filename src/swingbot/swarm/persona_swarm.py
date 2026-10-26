"""A minimal multi-round persona swarm with a ReportAgent aggregator.

Round 1: every persona reacts to the news on its own.
Rounds 2..n: every persona sees the previous round and may update (contagion).
The ReportAgent then turns the final round into one Signal.
"""

from __future__ import annotations

import math

from ..models import NewsEvent, Sentiment, Signal
from .base import SwarmEngine
from .personas import DEFAULT_PERSONAS, Persona
from .reasoners import PersonaReasoner, PersonaView, view_to_dict


class ReportAgent:
    """Conviction- and weight-averaged stance, with confidence cut by disagreement."""

    def __init__(self, neutral_band: float = 0.15):
        self.neutral_band = neutral_band

    def summarise(
        self, event: NewsEvent, views: list[PersonaView], weights: dict[str, float], horizon: str
    ) -> Signal:
        w = [weights.get(v.persona, 1.0) * max(v.conviction, 1e-6) for v in views]
        total = sum(w)
        score = sum(wi * v.stance for wi, v in zip(w, views)) / total if total else 0.0
        spread = (
            math.sqrt(sum(wi * (v.stance - score) ** 2 for wi, v in zip(w, views)) / total)
            if total
            else 1.0
        )
        agreement = max(0.0, 1.0 - spread)  # stance spread is at most 1 on a [-1, 1] scale
        avg_conviction = sum(v.conviction for v in views) / len(views)

        if score > self.neutral_band:
            sentiment = Sentiment.BULLISH
        elif score < -self.neutral_band:
            sentiment = Sentiment.BEARISH
        else:
            sentiment = Sentiment.NEUTRAL

        confidence = min(1.0, abs(score) * 0.5 + avg_conviction * 0.25 + agreement * 0.25)
        if sentiment is Sentiment.NEUTRAL:
            confidence = min(confidence, 0.5)

        return Signal(
            ticker=event.ticker,
            sentiment=sentiment,
            confidence=round(confidence, 4),
            time_horizon=horizon,
            score=round(score, 4),
            rationale=f"{len(views)} personas, crowd stance {score:+.2f}, agreement {agreement:.2f}",
            persona_views=[view_to_dict(v) for v in views],
        )


class PersonaSwarm(SwarmEngine):
    name = "persona"

    def __init__(
        self,
        reasoner: PersonaReasoner,
        personas: tuple[Persona, ...] = DEFAULT_PERSONAS,
        rounds: int = 2,
        horizon: str = "72h",
        report_agent: ReportAgent | None = None,
    ):
        if rounds < 1:
            raise ValueError("rounds must be >= 1")
        self.reasoner = reasoner
        self.personas = personas
        self.rounds = rounds
        self.horizon = horizon
        self.report_agent = report_agent or ReportAgent()
        self.name = f"persona-{getattr(reasoner, 'name', 'custom')}"

    def simulate(self, event: NewsEvent) -> Signal:
        views: list[PersonaView] | None = None
        for _ in range(self.rounds):
            views = [self.reasoner.react(p, event, views) for p in self.personas]
        weights = {p.name: p.weight for p in self.personas}
        return self.report_agent.summarise(event, views, weights, self.horizon)
