"""Core data types shared by the swarm, the price layer and the backtest."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# NSE's pre-open order entry closes at 09:08; the auction then sets the 09:15
# open from orders placed before it. News after 09:08 can't be in an order that
# trades at the open price, so it first trades at the next session's open.
AUCTION_CUTOFF = time(9, 8)


class Sentiment(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

    @property
    def direction(self) -> int:
        return {"bullish": 1, "bearish": -1, "neutral": 0}[self.value]


@dataclass(frozen=True)
class NewsEvent:
    """One historical news item: the "simulation seed" from blueprint Layer A.

    `published_at` must be timezone-aware. It decides which candle is the first
    one we could have traded, so getting it wrong leaks the future into the test.
    """

    event_id: str
    ticker: str
    published_at: datetime
    headline: str
    body: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.published_at.tzinfo is None:
            raise ValueError(f"{self.event_id}: published_at must be timezone-aware")


@dataclass(frozen=True)
class Signal:
    """The ReportAgent output, matching the blueprint JSON shape."""

    ticker: str
    sentiment: Sentiment
    confidence: float
    time_horizon: str = "72h"
    score: float = 0.0  # signed crowd stance in [-1, 1], kept for rank metrics
    rationale: str = ""
    persona_views: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    @property
    def horizon_hours(self) -> int:
        h = self.time_horizon.strip().lower()
        if h.endswith("h"):
            return int(h[:-1])
        if h.endswith("d"):
            return int(h[:-1]) * 24
        raise ValueError(f"unrecognised time_horizon {self.time_horizon!r}")

    def to_json(self) -> dict:
        return {
            "ticker": self.ticker,
            "sentiment": self.sentiment.value,
            "confidence": round(self.confidence, 4),
            "time_horizon": self.time_horizon,
        }

    def to_record(self) -> dict:
        d = asdict(self)
        d["sentiment"] = self.sentiment.value
        return d
