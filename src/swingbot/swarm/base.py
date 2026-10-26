"""The pluggable swarm contract.

Anything that turns a NewsEvent into a Signal is a SwarmEngine. The backtest
only ever sees this interface, so the persona stub, an LLM-backed swarm and a
future MiroFish adapter are interchangeable.

An engine must never be handed price data. It sees the news, nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import NewsEvent, Signal


class SwarmEngine(ABC):
    name: str = "base"

    @abstractmethod
    def simulate(self, event: NewsEvent) -> Signal:
        """Run the simulation for one event and return the ReportAgent signal."""
