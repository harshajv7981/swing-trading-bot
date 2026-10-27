"""The DhanHQ adapter, as a shape only. It places nothing and has no network code.

It shows where each `Broker` method lands at Dhan, so the daily loop can be
written against the interface now and pointed at Dhan's sandbox later. Every
method raises until the adapter is built, tested in Dhan's sandbox and the
build plan's live gate is met. Field names and endpoints below are from
dhanhq.co/docs/v2 and must be checked there before use:

- submit:       POST /orders (the ticket's entry: BUY, CNC, LIMIT, DAY), with
                securityId from Dhan's scrip master for tradingSymbol. The exits
                are not sent with it: Dhan takes a Forever Order (OCO) on
                delivery shares only from the next trading day, so the adapter
                places POST /forever/orders (orderFlag OCO, both legs MARKET)
                on the first sync after the shares are in holdings.
- cancel:       DELETE /orders/{orderId}, entries only.
- close:        a MARKET sell for the holding, after DELETE /forever/orders/{id}.
- modify_stop:  PUT /forever/orders/{id}, the stop leg's trigger price; refuse a lower one.
- sync:         GET /trades and GET /forever/orders since the last sync, mapped to
                `Fill`s with the contract note's charges as costs; and R1's check
                that every holding has a live stop within 60 seconds of the OCO
                being placed.
- open_orders:  GET /orders and GET /forever/orders.

Also the adapter's job, and not the paper broker's: R10 order rate limits,
SEBI's static IP and daily login, and refusing to run without the live flag.
"""

from __future__ import annotations

import pandas as pd

from .base import Fill, StopMove

NOT_BUILT = "The Dhan adapter is not built; paper trading runs on PaperBroker."


class DhanBroker:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(NOT_BUILT)

    def submit(self, ticket: dict, at) -> str:
        raise NotImplementedError(NOT_BUILT)

    def cancel(self, order_id: str, at) -> None:
        raise NotImplementedError(NOT_BUILT)

    def close(self, ticker: str, at) -> str:
        raise NotImplementedError(NOT_BUILT)

    def modify_stop(self, ticker: str, stop: float, at) -> StopMove:
        raise NotImplementedError(NOT_BUILT)

    def sync(self, as_of, bars: dict[str, pd.DataFrame]) -> list[Fill | StopMove]:
        raise NotImplementedError(NOT_BUILT)

    def open_orders(self) -> list[dict]:
        raise NotImplementedError(NOT_BUILT)
