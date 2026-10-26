"""The account the risk engine checks against, and the orders it sizes and gates.

In paper trading the portfolio file is kept by hand or by the paper ledger; in
the live phase the broker adapter will write it. Amounts are in rupees.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_SECTORS = Path("data/universe/sectors.csv")


@dataclass(frozen=True)
class Position:
    ticker: str
    qty: int
    entry: float
    stop: float
    sector: str = ""

    @property
    def open_risk(self) -> float:
        """Loss if the stop fills at its price. Zero once the stop is at or above the entry."""
        return self.qty * max(self.entry - self.stop, 0.0)


@dataclass(frozen=True)
class Portfolio:
    equity: float
    cash: float
    week_open_equity: float  # equity at the week's first session open (R5)
    day_open_equity: float   # equity at today's open (R6)
    positions: tuple[Position, ...] = ()

    @classmethod
    def from_json(cls, d: dict) -> "Portfolio":
        positions = tuple(Position(**p) for p in d.get("positions", []))
        return cls(float(d["equity"]), float(d["cash"]), float(d["week_open_equity"]),
                   float(d["day_open_equity"]), positions)

    @classmethod
    def load(cls, path: str | Path) -> "Portfolio":
        return cls.from_json(json.loads(Path(path).read_text()))

    def held(self, ticker: str) -> int:
        return sum(p.qty for p in self.positions if p.ticker == ticker.upper())

    @property
    def open_risk(self) -> float:
        return sum(p.open_risk for p in self.positions)


@dataclass(frozen=True)
class Order:
    ticker: str
    side: str  # "buy" opens or adds; "sell" reduces what is held
    qty: int
    entry: float
    stop: float | None = None
    target: float | None = None
    sector: str = ""
    product: str = "CNC"  # delivery; the bot never uses intraday or margin products
    plan: dict = field(default_factory=dict, compare=False)
    mood: dict = field(default_factory=dict, compare=False)  # the market mood it was sized under

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Order":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    @property
    def fingerprint(self) -> str:
        """Short hash of the fields that matter, so a verdict cannot be reused for an edited order."""
        core = {k: getattr(self, k) for k in ("ticker", "side", "qty", "entry", "stop", "target", "product")}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def load_sectors(path: str | Path | None = None) -> dict[str, str]:
    path = Path(path) if path else DEFAULT_SECTORS
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        return {r["symbol"].strip().upper(): r["sector"].strip() for r in csv.DictReader(fh)}
