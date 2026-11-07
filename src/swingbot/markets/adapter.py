"""The execution-adapter contract, and an in-memory reference adapter.

Every broker adapter for the normalized contracts implements `ExecutionAdapter`.
tests/test_adapter_conformance.py runs one suite against each of them:

    submit(intent)        -> OrderUpdate   idempotent on client_order_id
    cancel(client_id)     -> OrderUpdate   open orders only
    sync()                -> new Fills and status changes, each reported once
    positions()           -> broker-side holdings
    open_orders()         -> orders not yet final

`SimulatedAdapter` is the reference implementation: no network, fills only when
told to (`execute`), and the same send checks (halts, modes) as a real adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..broker.base import OrderRefused
from .contracts import Fill, OrderIntent, OrderStatus, OrderUpdate, Position
from .control import STATE_ROOT, ExecutionMode, may_send


class AdapterRefused(OrderRefused):
    """The adapter will not do this. The message is one plain sentence."""


class ExecutionAdapter(Protocol):
    market: str

    def submit(self, intent: OrderIntent) -> OrderUpdate: ...
    def cancel(self, client_order_id: str) -> OrderUpdate: ...
    def sync(self) -> list[OrderUpdate | Fill]: ...
    def positions(self) -> list[Position]: ...
    def open_orders(self) -> list[OrderUpdate]: ...


def net_positions(market: str, fills: list[Fill]) -> list[Position]:
    """Long-only holdings from fills: average cost of the buys, reduced by sells."""
    held: dict[str, tuple[int, float]] = {}
    for f in fills:
        qty, avg = held.get(f.symbol, (0, 0.0))
        if f.side == "buy":
            avg = (avg * qty + f.price * f.qty) / (qty + f.qty)
            qty += f.qty
        else:
            qty -= f.qty
        held[f.symbol] = (qty, avg if qty else 0.0)
    return [Position(market, s, q, a) for s, (q, a) in sorted(held.items()) if q]


@dataclass
class _SimOrder:
    intent: OrderIntent
    broker_order_id: str
    status: OrderStatus = OrderStatus.ACCEPTED
    filled: int = 0
    reported: OrderStatus = OrderStatus.ACCEPTED


@dataclass
class SimulatedAdapter:
    market: str
    mode: ExecutionMode = ExecutionMode.PAPER
    state_root: Path = STATE_ROOT
    orders: dict[str, _SimOrder] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    _pending: list[Fill] = field(default_factory=list)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _update(self, cid: str) -> OrderUpdate:
        o = self.orders[cid]
        return OrderUpdate(self.market, cid, o.broker_order_id, o.status, o.filled, self._now())

    def submit(self, intent: OrderIntent) -> OrderUpdate:
        if intent.market != self.market:
            raise AdapterRefused(f"this adapter trades {self.market} only, not {intent.market}.")
        if intent.client_order_id in self.orders:
            return self._update(intent.client_order_id)
        may_send(self.market, self.mode, self.state_root)
        self.orders[intent.client_order_id] = _SimOrder(intent, f"sim-{len(self.orders) + 1}")
        return self._update(intent.client_order_id)

    def cancel(self, client_order_id: str) -> OrderUpdate:
        o = self.orders.get(client_order_id)
        if o is None:
            raise AdapterRefused(f"no order {client_order_id} in this book.")
        if o.status.final:
            raise AdapterRefused(f"{client_order_id} is already {o.status.value}.")
        o.status = o.reported = OrderStatus.CANCELLED
        return self._update(client_order_id)

    def sync(self) -> list[OrderUpdate | Fill]:
        events: list[OrderUpdate | Fill] = list(self._pending)
        self.fills += self._pending
        self._pending = []
        for cid, o in self.orders.items():
            if o.status is not o.reported:
                o.reported = o.status
                events.append(self._update(cid))
        return events

    def positions(self) -> list[Position]:
        return net_positions(self.market, self.fills)

    def open_orders(self) -> list[OrderUpdate]:
        return [self._update(c) for c, o in self.orders.items() if not o.status.final]

    # --- the venue ------------------------------------------------------------------

    def execute(self, client_order_id: str, qty: int, price: float) -> None:
        o = self.orders[client_order_id]
        o.filled += qty
        o.status = OrderStatus.FILLED if o.filled >= o.intent.qty else OrderStatus.PARTIALLY_FILLED
        n = sum(len(x) for x in (self.fills, self._pending)) + 1
        self._pending.append(Fill(self.market, f"simfill-{n}", client_order_id, o.broker_order_id,
                                  o.intent.symbol, o.intent.side, qty, price, 0.0, self._now()))

    def reject(self, client_order_id: str) -> None:
        self.orders[client_order_id].status = OrderStatus.REJECTED

    def expire(self, client_order_id: str) -> None:
        self.orders[client_order_id].status = OrderStatus.EXPIRED
