"""Normalized order contracts, the same for every market and broker.

An `OrderIntent` is what a strategy (or an agent, after deterministic
validation) wants; adapters turn it into a broker payload and report back with
`OrderUpdate`s and `Fill`s. All times are UTC; market-local time is derived from
the profile for display and session rules. These sit beside the NSE ticket
flow in broker/base.py, which is unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum

from .control import utc_stamp
from .profiles import MarketProfile, get_profile

SIDES = ("buy", "sell")


class ContractError(ValueError):
    """The record breaks the contract. The message is one plain sentence."""


def _profile(market: str) -> MarketProfile:
    try:
        return get_profile(market)
    except KeyError as e:
        raise ContractError(f"unknown market {market!r}") from e


def _aware(at: datetime, what: str) -> None:
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise ContractError(f"{what} must be a timezone-aware datetime")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


class OrderStatus(str, Enum):
    NEW = "new"                      # built here, not yet acknowledged by the broker
    ACCEPTED = "accepted"            # the broker holds it, nothing filled
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def final(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED)


@dataclass(frozen=True)
class OrderIntent:
    market: str
    client_order_id: str            # ours, and the broker's idempotency key
    symbol: str
    side: str
    qty: int
    order_type: str                 # market | limit | stop | stop_limit
    time_in_force: str
    created_utc: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    strategy: str = ""
    strategy_version: str = ""

    def __post_init__(self) -> None:
        p = _profile(self.market)
        if not self.client_order_id:
            raise ContractError("an order needs a client order id")
        if not self.symbol:
            raise ContractError("an order needs a symbol")
        if self.side not in SIDES:
            raise ContractError(f"side must be buy or sell, not {self.side!r}")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int):
            raise ContractError(f"quantity must be a whole number of shares, not {self.qty!r}")
        if self.qty <= 0:
            raise ContractError("quantity must be positive")
        if self.order_type not in p.order_types:
            raise ContractError(f"order type {self.order_type!r} is not allowed in {p.name}")
        if self.time_in_force not in p.time_in_force:
            raise ContractError(f"time in force {self.time_in_force!r} is not allowed in {p.name}")
        _aware(self.created_utc, "created_utc (timezone)")
        needs_limit = self.order_type in ("limit", "stop_limit")
        needs_stop = self.order_type in ("stop", "stop_limit")
        for name, value, needed in (("limit price", self.limit_price, needs_limit),
                                    ("stop price", self.stop_price, needs_stop)):
            if needed and value is None:
                raise ContractError(f"a {self.order_type} order needs a {name}")
            if not needed and value is not None:
                raise ContractError(f"a {self.order_type} order takes no {name}")
            if value is not None:
                if value <= 0:
                    raise ContractError(f"{name} must be positive")
                if not p.on_tick(value):
                    raise ContractError(f"{name} {value} is not on the {p.tick(value)} tick in {p.name}")

    @property
    def profile(self) -> MarketProfile:
        return get_profile(self.market)

    @property
    def notional(self) -> float | None:
        price = self.limit_price or self.stop_price
        return None if price is None else self.qty * price

    def to_record(self) -> dict:
        rec = {"kind": "order_intent", **asdict(self)}
        rec["created_utc"] = utc_stamp(self.created_utc)
        return rec

    @classmethod
    def from_record(cls, rec: dict) -> OrderIntent:
        fields = {k: v for k, v in rec.items() if k != "kind"}
        fields["created_utc"] = _parse_utc(fields["created_utc"])
        return cls(**fields)


@dataclass(frozen=True)
class OrderUpdate:
    market: str
    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    filled_qty: int
    at_utc: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        _profile(self.market)
        _aware(self.at_utc, "at_utc")

    def to_record(self) -> dict:
        rec = {"kind": "order_update", **asdict(self)}
        rec["status"], rec["at_utc"] = self.status.value, utc_stamp(self.at_utc)
        return rec


@dataclass(frozen=True)
class Fill:
    market: str
    fill_id: str                    # the broker's execution id: the deduplication key
    client_order_id: str
    broker_order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    fees: float
    at_utc: datetime

    def __post_init__(self) -> None:
        _profile(self.market)
        if not self.fill_id or not self.broker_order_id:
            raise ContractError("a fill needs the broker's fill id and order id")
        if self.side not in SIDES:
            raise ContractError(f"side must be buy or sell, not {self.side!r}")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty <= 0:
            raise ContractError(f"fill quantity must be a positive whole number, not {self.qty!r}")
        if not self.price > 0:
            raise ContractError("fill price must be positive")
        _aware(self.at_utc, "at_utc")

    @property
    def currency(self) -> str:
        return get_profile(self.market).currency

    @property
    def notional(self) -> float:
        return self.qty * self.price

    def to_record(self) -> dict:
        rec = {"kind": "fill", "currency": self.currency, **asdict(self)}
        rec["at_utc"] = utc_stamp(self.at_utc)
        return rec


@dataclass(frozen=True)
class Position:
    market: str
    symbol: str
    qty: int
    avg_price: float

    def __post_init__(self) -> None:
        _profile(self.market)

    @property
    def currency(self) -> str:
        return get_profile(self.market).currency

    @property
    def cost(self) -> float:
        return self.qty * self.avg_price
