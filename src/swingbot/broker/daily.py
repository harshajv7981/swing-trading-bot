"""The paper broker's two hooks for the daily loop (`swingbot-daily`).

Point config/daily.toml at them:

    broker_settle = "swingbot.broker.daily:settle"
    broker_submit = "swingbot.broker.daily:submit"

The book is `<state_dir>/paper_broker.json`, beside the ledger's other state.
Settling trails held stops by R7 after each close. Nothing here talks to a
real broker.
"""

from __future__ import annotations

from pathlib import Path

from ..data.router import DataError, Router
from ..paper.ledger import Ledger
from ..risk.gate import KillSwitch
from ..technicals import bars_known_at
from .base import OrderRefused, record_fills
from .paper import PaperBroker


def _broker(day) -> PaperBroker:
    return PaperBroker(Path(day.state_dir) / "paper_broker.json", day.risk, day.calendar, trail=True)


def settle(day):
    """Fill or lapse the DAY entries and fire OCO exits, for every session closed by `day.as_of`."""
    from ..daily.hooks import FAILED, OK, Step

    broker = _broker(day)
    router = Router(day.data)
    bars, missing = {}, []
    for t in sorted({o["ticker"] for o in broker.open_orders()}):
        try:
            bars[t] = bars_known_at(router.fetch("prices", t, day.as_of)[1], day.as_of)
        except DataError:
            missing.append(t)
    events = broker.sync(day.as_of, bars)
    record_fills(Ledger(day.ledger), events)  # the ledger first, then the book
    broker.save()
    for n in broker.notes:
        day.note(f"paper broker: {n}")
    fills = [e for e in events if hasattr(e, "side")]
    detail = (f"{len(fills)} fill(s), {len(events) - len(fills)} stop move(s), "
              f"{len(broker.open_orders())} order(s) live; settled to {broker.last_session or 'nothing yet'}")
    if fills:
        detail += ": " + "; ".join(f"{e.side} {e.qty} {e.ticker} @ {e.price:,.2f} ({e.reason})" for e in fills)
    if missing:
        return Step("paper broker settle", FAILED, f"no prices for {', '.join(missing)}; {detail}")
    return Step("paper broker settle", OK, detail)


def submit(day, tickets: list[dict]):
    """Hand today's PASSed tickets to the paper broker. Each entry is live for the next session only."""
    from ..daily.hooks import BLOCKED, OK, Step

    if not tickets:
        return Step("paper broker submit", OK, "no tickets today")
    broker = _broker(day)
    latched = KillSwitch(day.state_dir).latched
    taken, refused = [], []
    for t in tickets:
        try:
            oid = broker.submit(t, day.stamp, kill_switch_latched=latched)
            o = broker.orders[-1]
            taken.append(f"{oid} buy {o['qty']} {o['ticker']} limit {o['limit']:,.2f} on {o['session']}")
        except OrderRefused as e:
            refused.append(f"{t.get('entry', {}).get('tradingSymbol', '?')}: {e}")
    broker.save()
    detail = "; ".join(taken + [f"refused {r}" for r in refused])
    return Step("paper broker submit", BLOCKED if refused and not taken else OK, detail)
