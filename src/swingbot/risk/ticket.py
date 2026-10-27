"""Order tickets: one PASSed order written out as the broker orders it would become.

The idea of a last agent that turns a sized, risk-checked trade into a
structured order (type, quantity, entry, stop, target, validity) comes from
The Swarm Corporation's AutoHedge (MIT, github.com/The-Swarm-Corporation/AutoHedge,
Execution-Agent). There an LLM writes the order. Here it is plain code that
copies the numbers the risk engine already fixed and adds nothing of its own.

A ticket is paper only. Nothing in this repo sends it anywhere. It has two parts:

- entry: a DAY limit buy, CNC (delivery), for the sized quantity.
- exits: once the entry fills, one OCO pair for the filled quantity, kept at the
  broker across days (a Dhan Forever Order with the OCO flag, per the build
  plan). Both legs are MARKET: the target leg sells at market when the price
  reaches the target, and the stop leg is a stop-loss market sell that triggers
  at the stop.

Why market and not a stop-limit: a stop-limit with its limit a little under the
trigger does not fill when the stock opens below the limit (a gap down on
results or bad news), so the stop is simply missed and the loss keeps growing.
A stop-loss market sells at about the open instead. The loss is then bigger
than 1R, but it is taken. The R9 gap allowance stays a sizing estimate only
(loss_with_gap); a real gap can be wider. Dhan's Forever Order takes one
orderType for the whole OCO, LIMIT or MARKET (dhanhq.co/docs/v2/forever), so
MARKET also applies to the target leg; for large caps that pass R8 the slippage
there is small, and a gap up through the target sells at the better open.

Dhan only accepts a Forever Order for delivery shares from the next trading
day, so the first night after the entry has no stop at the broker.

Field names follow DhanHQ where they are plain (NSE_EQ, CNC, LIMIT, DAY). The
mapping to Dhan's own request fields, and the security id from its scrip
master, belong to the broker adapter and must be checked in Dhan's sandbox.

A ticket is refused unless the latest verdict in the gate's log for this exact
order (same fingerprint) is a PASS from the same day, and the kill switch is
not latched now.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .config import RiskConfig
from .exits import round_down
from .portfolio import Order


class TicketRefused(ValueError):
    """No ticket for this order. The message is one plain sentence."""


def latest_verdict(log: Path, fingerprint: str) -> dict | None:
    """The last verdict the gate logged for this fingerprint, or None."""
    if not log.exists():
        return None
    found = None
    for line in log.read_text().splitlines():
        if line.strip():
            v = json.loads(line)
            if v.get("fingerprint") == fingerprint:
                found = v
    return found


def make_ticket(order: Order, verdict: dict | None, today: date, config: RiskConfig,
                kill_switch_latched: bool = False) -> dict:
    if kill_switch_latched:
        raise TicketRefused("The weekly kill switch is latched, so no entry ticket is written until Harsha resets it.")
    if order.side != "buy":
        raise TicketRefused("Tickets are for long entries only; this order is a sell.")
    if order.product != "CNC":
        raise TicketRefused(f"The bot only uses CNC (delivery), not {order.product}.")
    if order.stop is None or order.target is None:
        raise TicketRefused("The order has no stop or no target, so there is no exit to put at the broker.")
    if verdict is None:
        raise TicketRefused(f"The gate has no verdict for this order [{order.fingerprint}]; run swingbot-risk check first.")
    if verdict.get("status") != "PASS":
        raise TicketRefused(f"The gate's latest verdict for this order is {verdict.get('status')}, not PASS.")
    checked = str(verdict.get("at", ""))[:10]
    if checked != today.isoformat():
        raise TicketRefused(f"The PASS is from {checked or 'an unknown day'}, not {today}; check the order again today.")

    stop_with_gap = round_down(order.stop * (1 - config.gap_allowance_pct / 100), config.tick)
    common = {"exchangeSegment": "NSE_EQ", "productType": "CNC", "tradingSymbol": order.ticker,
              "securityId": None}
    return {
        "paper_only": True,
        "fingerprint": order.fingerprint,
        "checked_at": verdict["at"],
        "entry": {**common, "transactionType": "BUY", "orderType": "LIMIT", "validity": "DAY",
                  "quantity": order.qty, "price": order.entry},
        "exits": {
            "when": "for the filled quantity, from the next trading day (Dhan takes no Forever Order "
                    "on shares bought today)",
            "orderFlag": "OCO",
            "orderType": "MARKET",
            "target": {**common, "transactionType": "SELL", "orderType": "MARKET",
                       "quantity": order.qty, "triggerPrice": order.target},
            "stop": {**common, "transactionType": "SELL", "orderType": "STOP_LOSS_MARKET",
                     "quantity": order.qty, "triggerPrice": order.stop},
        },
        "if_unfilled": "the DAY entry lapses at the close; a new day needs a new plan, size and check",
        "amount": round(order.qty * order.entry, 2),
        "loss_at_stop": round(order.qty * (order.entry - order.stop), 2),
        "loss_with_gap": round(order.qty * (order.entry - stop_with_gap), 2),
    }


def describe(t: dict) -> str:
    e, x = t["entry"], t["exits"]
    return (f"PAPER  buy {e['quantity']} {e['tradingSymbol']} limit {e['price']:,.2f} (CNC, DAY) = {t['amount']:,.0f}\n"
            f"  then OCO (market) for the filled qty: target {x['target']['triggerPrice']:,.2f} | "
            f"stop-loss market {x['stop']['triggerPrice']:,.2f}\n"
            f"  loss at the stop {t['loss_at_stop']:,.0f} ({t['loss_with_gap']:,.0f} with the gap allowance; "
            f"a bigger gap fills at the open)"
            f"  [{t['fingerprint']}]")
