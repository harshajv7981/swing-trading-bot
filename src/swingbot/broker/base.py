"""The broker interface, and the step that writes broker fills to the paper ledger.

Two brokers sit behind it. `PaperBroker` fills tickets from daily bars and is
what paper trading runs on. `DhanBroker` (broker/dhan.py) talks to Dhan's
sandbox, with live money off (R11). Whatever calls a broker (the daily loop) only uses the methods
below, so swapping one for the other is a constructor change:

    broker.submit(ticket, at)            -> order id: a ticket from `swingbot-risk ticket`
    broker.cancel(order_id, at)          -> cancel an entry that has not filled
    broker.close(ticker, at)             -> sell a holding at the next open, dropping its OCO
    broker.modify_stop(ticker, stop, at) -> move a held position's stop (it may only go up)
    broker.sync(as_of, bars)             -> the fills since the last sync
    broker.open_orders()                 -> the book: live entries and live OCO exits

`record_fills` turns those fills into ledger lines, so the paper account is
replayed from what the broker did, never from prices typed in by hand.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

import pandas as pd

from ..paper.ledger import Ledger, default_costs


class OrderRefused(ValueError):
    """The broker will not take this. The message is one plain sentence."""


@dataclass(frozen=True)
class Fill:
    order_id: str
    at: str              # "YYYY-MM-DD HH:MM" IST
    ticker: str
    side: str            # buy or sell
    qty: int
    price: float
    reason: str          # entry, entry-gap, target, target-gap, stop, stop-gap, stop-limit, time, close
    stop: float | None = None     # buys: the stop the OCO was placed with
    target: float | None = None
    sector: str = ""
    fingerprint: str = ""
    verdict: str = ""             # buys: PASS when the ticket came from a PASSed order
    costs: float | None = None    # None: the ledger's default estimate

    def to_record(self) -> dict:
        rec = {"kind": "fill", "source": "paper-broker", **asdict(self)}
        if rec["costs"] is None:
            rec["costs"] = default_costs(self.qty, self.price)
        if self.side == "sell":
            for k in ("stop", "target", "sector", "fingerprint", "verdict"):
                rec.pop(k)
        return rec


@dataclass(frozen=True)
class StopMove:
    order_id: str
    at: str
    ticker: str
    stop: float
    reason: str

    def to_record(self) -> dict:
        return {"kind": "stop", "source": "paper-broker", **asdict(self)}


class Broker(Protocol):
    def submit(self, ticket: dict, at: str) -> str: ...
    def cancel(self, order_id: str, at: str) -> None: ...
    def close(self, ticker: str, at: str) -> str: ...
    def modify_stop(self, ticker: str, stop: float, at: str) -> StopMove: ...
    def sync(self, as_of, bars: dict[str, pd.DataFrame]) -> list[Fill | StopMove]: ...
    def open_orders(self) -> list[dict]: ...


def record_fills(ledger: Ledger, events: list[Fill | StopMove]) -> int:
    """Append what the broker did to the ledger, in order. Returns the number written."""
    for e in events:
        ledger.append(e.to_record())
    return len(events)
