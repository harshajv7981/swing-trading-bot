"""Placeholder adapter for MiroFish.

MiroFish is the swarm engine named in the blueprint. Its API has not been
evaluated yet, so this adapter only fixes the contract: take a NewsEvent, build
a simulation seed, run it, and map the report back to a Signal. Fill in
`_run` once a MiroFish container is reachable.
"""

from __future__ import annotations

from ..models import NewsEvent, Sentiment, Signal
from .base import SwarmEngine


class MiroFishEngine(SwarmEngine):
    name = "mirofish"

    def __init__(self, base_url: str = "http://localhost:8000", horizon: str = "72h"):
        self.base_url = base_url
        self.horizon = horizon

    def seed(self, event: NewsEvent) -> dict:
        return {
            "ticker": event.ticker,
            "exchange": "NSE",
            "published_at": event.published_at.isoformat(),
            "headline": event.headline,
            "body": event.body,
            "horizon": self.horizon,
        }

    def _run(self, seed: dict) -> dict:
        raise NotImplementedError(
            "MiroFish integration is not built yet. Use --engine lexicon or --engine claude."
        )

    def simulate(self, event: NewsEvent) -> Signal:
        report = self._run(self.seed(event))
        return Signal(
            ticker=report["ticker"],
            sentiment=Sentiment(report["sentiment"]),
            confidence=float(report["confidence"]),
            time_horizon=report.get("time_horizon", self.horizon),
            score=float(report.get("score", 0.0)),
        )
