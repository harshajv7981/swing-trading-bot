"""The paper broker: takes tickets and fills them from daily bars, the way NSE would.

It holds an order book like a broker does, settles it one closed session at a
time, and reports fills for the paper ledger. It never talks to anything
outside this repo.

How a session settles, for each stock, with only its open, high, low and close:

Entry (a DAY limit buy, live for one session only):
- opens at or under the limit: filled at the open (the opening auction), 09:15.
- else the low trades *under* the limit: filled at the limit. Touching it is
  not enough, because there is a queue ahead of us at that price.
- else it lapses at the close.

Exits (one OCO pair for the filled quantity, kept across sessions). Dhan takes
no Forever Order on shares bought today, so the OCO is live from the session
after the entry: the entry's own session and the night after it have no stop
at the broker. Both legs are MARKET, as the ticket writes them:
- opens at or under the stop: the stop triggers at the open and sells at the
  open less slippage. The loss is then bigger than 1R, but it is taken.
- opens at or over the target: the target sells at the open less slippage.
- else the low touches the stop: it sells at the stop less slippage. When the
  same bar also reaches the target, the stop is taken: daily bars cannot say
  which came first, so the worse one is assumed.
- else the high reaches the target: it sells at the target less slippage.
An older ticket with a stop-limit leg (orderType STOP_LOSS) still works: on a
gap under its limit it waits there, fills if the high gets back to it, and
otherwise lapses at the close, leaving the position with no stop. That is why
the ticket moved to a stop-loss market.

Time exit: a ticket with a `hold` plan (signal-to-order writes one from the
call's horizon) is sold at the close of its `exit_by` session, less slippage,
when neither leg has filled by then. A stock with no bar that day sells at the
close of its next bar. This is the backtest's exit, so paper and walk-forward
trades end where the research measured them.

A close sells at the next session's open and cancels the OCO. Trailing (R7,
`trail_stop`) runs after each close when asked and moves the stop for the next
session; stops never move down.

Splits and bonuses (`swingbot.corporate`): on an ex-date, a live order placed
before the action was noted gets its quantity multiplied and its prices divided
by the ratio, so it keeps matching the adjusted bars it settles on.

Fill times are 09:15 for the open and 15:30 for anything during the session,
whose real time a daily bar does not have.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .. import corporate
from ..models import IST
from ..risk.calendar import NSECalendar
from ..risk.config import RiskConfig
from ..risk.exits import round_down, trail_stop
from ..risk.portfolio import load_sectors
from ..technicals import MARKET_CLOSE
from .base import Fill, OrderRefused, StopMove

DEFAULT_BOOK = Path("data/state/paper_broker.json")
DEFAULT_SLIPPAGE_BPS = 5.0  # every market sell (stop, target, close) fills this far under its price
OPEN, INTRADAY, AFTER_CLOSE = "09:15", "15:30", "15:35"


def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


class PaperBroker:
    """The book lives in a JSON file. Methods change it in memory; `save()` writes it.

    Save only after the fills from `sync` are in the ledger, so a failed ledger
    write never leaves the book ahead of the account.
    """

    def __init__(self, path: str | Path = DEFAULT_BOOK, config: RiskConfig | None = None,
                 calendar: NSECalendar | None = None, slippage_bps: float = DEFAULT_SLIPPAGE_BPS,
                 trail: bool = False, actions: dict | None = None):
        self.path = Path(path)
        self.actions = actions or {}   # corporate.load(state_dir): splits and bonuses
        self.config = config or RiskConfig()
        self.calendar = calendar or NSECalendar()
        self.slippage_bps = slippage_bps
        self.trail = trail
        state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.orders: list[dict] = state.get("orders", [])
        self.last_session: str | None = state.get("last_session")
        self.notes: list[str] = []  # things the last sync wants a person to see

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_session": self.last_session, "orders": self.orders}, indent=2) + "\n")
        os.replace(tmp, self.path)

    # --- the book ------------------------------------------------------------

    def open_orders(self) -> list[dict]:
        return [o for o in self.orders if o["status"] == "open"]

    def _live(self, ticker: str) -> list[dict]:
        return [o for o in self.open_orders() if o["ticker"] == ticker]

    def _new_id(self) -> str:
        return f"P{len(self.orders) + 1:05d}"

    def _next_session(self, at: pd.Timestamp) -> date:
        day = at.tz_localize(None).date()
        if at.time() >= MARKET_CLOSE:
            day += timedelta(days=1)
        while not self.calendar.is_session(day):
            day += timedelta(days=1)
        return day

    def submit(self, ticket: dict, at, kill_switch_latched: bool = False) -> str:
        """Take a ticket from `swingbot-risk ticket`. The entry is live for the next session only."""
        if ticket.get("paper_only") is not True:
            raise OrderRefused("The ticket is not marked paper_only, so the paper broker will not take it.")
        if kill_switch_latched:
            raise OrderRefused("The weekly kill switch is latched, so no new entry goes in until Harsha resets it.")
        e, x = ticket["entry"], ticket["exits"]
        ticker = e["tradingSymbol"].upper()
        if e["transactionType"] != "BUY" or e["orderType"] != "LIMIT" or e["productType"] != "CNC":
            raise OrderRefused("The entry must be a CNC limit buy.")
        if x.get("orderFlag") != "OCO":
            raise OrderRefused("The exits must be one OCO pair.")
        if self._live(ticker):
            raise OrderRefused(f"{ticker} already has a live order or position here; the bot does not add.")
        at = _ts(at)
        session = self._next_session(at)
        if self.last_session and session.isoformat() <= self.last_session:
            raise OrderRefused(f"The book has already settled {self.last_session}; an entry for {session} is too late.")
        checked = ticket.get("checked_at")
        if not checked or not ticket.get("fingerprint"):
            raise OrderRefused("The ticket has no PASS time or fingerprint from the risk gate.")
        checked_day = _ts(checked).tz_localize(None).date()
        if self.calendar.sessions_between(checked_day, session) > 1:
            raise OrderRefused(f"The PASS is from {checked_day}; the entry would go in on {session}. Check it again.")

        qty, limit = int(e["quantity"]), float(e["price"])
        exit_by = (ticket.get("hold") or {}).get("exit_by")
        if exit_by and exit_by < session.isoformat():
            raise OrderRefused(f"{ticker}: the time exit {exit_by} is before the entry session {session}.")
        stop_leg, target_leg = x["stop"], x["target"]
        stop = float(stop_leg["triggerPrice"])
        stop_limit = float(stop_leg["price"]) if stop_leg["orderType"] == "STOP_LOSS" else None
        target = float(target_leg.get("triggerPrice") or target_leg["price"])
        if qty <= 0 or not stop < limit < target:
            raise OrderRefused(f"{ticker}: the ticket needs a positive quantity and stop < entry < target.")
        oid = self._new_id()
        self.orders.append({
            "id": oid, "type": "entry", "status": "open", "ticker": ticker, "qty": qty, "limit": limit,
            "session": session.isoformat(), "submitted_at": f"{at:%Y-%m-%d %H:%M}", "stop": stop,
            "stop_limit": stop_limit, "target": target, "fingerprint": ticket["fingerprint"],
            "checked_at": checked, "sector": ticket.get("sector") or load_sectors().get(ticker, ""),
            "exit_by": exit_by,
        })
        return oid

    def cancel(self, order_id: str, at) -> None:
        o = next((o for o in self.orders if o["id"] == order_id), None)
        if o is None or o["status"] != "open":
            raise OrderRefused(f"No live order {order_id}.")
        if o["type"] != "entry":
            raise OrderRefused("A held position's exits stay at the broker; use close to sell it.")
        o.update(status="cancelled", closed_at=f"{_ts(at):%Y-%m-%d %H:%M}")

    def close(self, ticker: str, at) -> str:
        """Sell a held position at the next session's open; the OCO is cancelled when it fills."""
        ticker = ticker.upper()
        oco = next((o for o in self._live(ticker) if o["type"] == "oco"), None)
        if oco is None:
            raise OrderRefused(f"{ticker} is not held here.")
        if any(o["type"] == "close" for o in self._live(ticker)):
            raise OrderRefused(f"{ticker} already has a close order in.")
        at = _ts(at)
        oid = self._new_id()
        self.orders.append({"id": oid, "type": "close", "status": "open", "ticker": ticker, "qty": oco["qty"],
                            "session": self._next_session(at).isoformat(), "submitted_at": f"{at:%Y-%m-%d %H:%M}",
                            "oco": oco["id"]})
        return oid

    def modify_stop(self, ticker: str, stop: float, at, reason: str = "moved by hand") -> StopMove:
        ticker = ticker.upper()
        oco = next((o for o in self._live(ticker) if o["type"] == "oco"), None)
        if oco is None:
            raise OrderRefused(f"{ticker} has no live stop here.")
        if stop < oco["stop"]:
            raise OrderRefused(f"{ticker}: the stop only moves up (now {oco['stop']:,.2f}, asked {stop:,.2f}).")
        if stop >= oco["target"]:
            raise OrderRefused(f"{ticker}: a stop at or above the target {oco['target']:,.2f} makes no sense.")
        self._set_stop(oco, stop)
        return StopMove(oco["id"], f"{_ts(at):%Y-%m-%d %H:%M}", ticker, stop, reason)

    def _set_stop(self, oco: dict, stop: float) -> None:
        if oco["stop_limit"] is not None:  # keep the limit the same distance under the trigger
            oco["stop_limit"] = round_down(stop - (oco["stop"] - oco["stop_limit"]), self.config.tick)
        oco["stop"] = stop

    # --- settling ------------------------------------------------------------

    def sync(self, as_of, bars: dict[str, pd.DataFrame]) -> list[Fill | StopMove]:
        """Settle every session closed by `as_of` that all live tickers have a bar for.

        `bars` per ticker must already be cut to what had closed by `as_of`
        (technicals.bars_known_at). A stock whose prices are behind holds the
        whole book back rather than letting an order lapse unseen.
        """
        self.notes = []
        as_of_day = _ts(as_of).tz_localize(None).normalize()
        live = sorted({o["ticker"] for o in self.open_orders()})
        missing = [t for t in live if t not in bars or bars[t].empty]
        if missing:
            self.notes.append(f"no prices for {', '.join(missing)}; nothing settled")
            return []
        cutoff = min([as_of_day] + [bars[t].index[-1] for t in live])
        start = pd.Timestamp(self.last_session) if self.last_session else None
        days = sorted({d for t in live for d in bars[t].index if (start is None or d > start) and d <= cutoff})
        events: list[Fill | StopMove] = []
        for day in days:
            self._corporate(f"{day:%Y-%m-%d}")
            for t in live:
                if day in bars[t].index:
                    events += self._settle(t, day, bars[t])
            for o in self.open_orders():  # a DAY order whose session passed with no bar for it
                if o["type"] in ("entry", "close") and o["session"] <= f"{day:%Y-%m-%d}":
                    o.update(status="expired", closed_at=f"{day:%Y-%m-%d} {INTRADAY}")
                    self.notes.append(f"{o['id']} {o['ticker']}: no trade on {o['session']}, so it lapsed")
        if days:
            self.last_session = f"{days[-1]:%Y-%m-%d}"
        if cutoff < as_of_day and live:
            self.notes.append(f"settled to {cutoff:%Y-%m-%d}; prices for a live ticker end there")
        return events

    def _corporate(self, d: str) -> None:
        """Move live orders onto the adjusted bars on a split or bonus ex-date (see the module notes)."""
        tick = self.config.tick
        for o in self.open_orders():
            for a in self.actions.get(o["ticker"], []):
                ex = a.ex_date.isoformat()
                if ex > d or ex in o.get("actions", []) or o.get("submitted_at", "") >= a.noted:
                    continue
                r = a.ratio
                o["qty"] = corporate.shares(o["qty"], r)
                for k in ("limit", "entry_price"):
                    if o.get(k) is not None:
                        o[k] = round(o[k] / r, 2)
                for k in ("stop", "initial_stop", "stop_limit", "target"):
                    if o.get(k):
                        o[k] = round_down(o[k] / r, tick)
                o["actions"] = o.get("actions", []) + [ex]
                self.notes.append(f"{o['id']} {o['ticker']}: {a.ratio:g}-for-1 split or bonus on {ex}; "
                                  f"now {o['qty']} shares, stop {o['stop']:,.2f}")

    def _settle(self, ticker: str, day: pd.Timestamp, b: pd.DataFrame) -> list[Fill | StopMove]:
        row = b.loc[day]
        o_, h_, l_ = (float(row[k]) for k in ("open", "high", "low"))
        d = f"{day:%Y-%m-%d}"
        out: list[Fill | StopMove] = []
        live = self._live(ticker)
        oco = next((o for o in live if o["type"] == "oco"), None)

        close_order = next((o for o in live if o["type"] == "close" and o["session"] == d), None)
        if close_order and oco:
            out.append(self._exit(oco, f"{d} {OPEN}", self._slip(o_), "close"))
            close_order.update(status="filled", closed_at=f"{d} {OPEN}", price=o_)
            return out

        if oco and oco["live_from"] <= d:
            out += self._exits(oco, d, o_, h_, l_)
        elif not oco:
            entry = next((o for o in live if o["type"] == "entry" and o["session"] == d), None)
            if entry:
                if o_ <= entry["limit"]:
                    price, at, reason = o_, f"{d} {OPEN}", "entry" if o_ == entry["limit"] else "entry-gap"
                elif l_ < entry["limit"]:
                    price, at, reason = entry["limit"], f"{d} {INTRADAY}", "entry"
                else:
                    entry.update(status="expired", closed_at=f"{d} {INTRADAY}")
                    self.notes.append(f"{entry['id']} {ticker}: low {l_:,.2f} never traded under the limit "
                                      f"{entry['limit']:,.2f}; the entry lapsed")
                    return out
                entry.update(status="filled", closed_at=at, price=price)
                out.append(Fill(entry["id"], at, ticker, "buy", entry["qty"], price, reason, entry["stop"],
                                entry["target"], entry["sector"], entry["fingerprint"], "PASS"))
                oco = {"id": self._new_id(), "type": "oco", "status": "open", "ticker": ticker, "qty": entry["qty"],
                       "entry_id": entry["id"], "entry_price": price, "entry_session": d,
                       "live_from": self._next_session(_ts(f"{d} {AFTER_CLOSE}")).isoformat(), "stop": entry["stop"],
                       "initial_stop": entry["stop"], "stop_limit": entry["stop_limit"], "target": entry["target"],
                       "exit_by": entry.get("exit_by"), "submitted_at": entry["submitted_at"],
                       "actions": list(entry.get("actions", []))}
                self.orders.append(oco)

        if oco and oco["status"] == "open" and oco.get("exit_by") and d >= oco["exit_by"]:
            out.append(self._exit(oco, f"{d} {INTRADAY}", self._slip(float(row["close"])), "time"))
            return out

        if oco and oco["status"] == "open" and self.trail:
            new, why = trail_stop(b[b.index <= day], oco["entry_session"], oco["entry_price"],
                                  oco["initial_stop"], oco["stop"], self.config)
            if new > oco["stop"] and new < oco["target"]:
                self._set_stop(oco, new)
                out.append(StopMove(oco["id"], f"{d} {AFTER_CLOSE}", ticker, new, f"R7 trail: {why}"))
        return out

    def _exits(self, oco: dict, d: str, o_: float, h_: float, l_: float) -> list[Fill]:
        stop, target, limit = oco["stop"], oco["target"], oco["stop_limit"]
        slip = self._slip
        if o_ <= stop:
            if limit is None:
                return [self._exit(oco, f"{d} {OPEN}", slip(o_), "stop-gap")]
            if o_ >= limit:
                return [self._exit(oco, f"{d} {OPEN}", o_, "stop-gap")]
            if h_ >= limit:
                return [self._exit(oco, f"{d} {INTRADAY}", limit, "stop-limit")]
            self.notes.append(f"{oco['ticker']} opened at {o_:,.2f}, under the stop-limit {limit:,.2f}, and "
                              "never came back: the stop lapsed unfilled and the position has NO stop now")
            oco.update(stop_lapsed=d, stop=0.0, stop_limit=None)
            return []
        if o_ >= target:
            return [self._exit(oco, f"{d} {OPEN}", slip(o_), "target-gap")]
        if l_ <= stop:
            return [self._exit(oco, f"{d} {INTRADAY}", slip(stop) if limit is None else max(slip(stop), limit),
                               "stop")]
        if h_ >= target:
            return [self._exit(oco, f"{d} {INTRADAY}", slip(target), "target")]
        return []

    def _slip(self, price: float) -> float:
        return round_down(price * (1 - self.slippage_bps / 1e4), self.config.tick)

    def _exit(self, oco: dict, at: str, price: float, reason: str) -> Fill:
        oco.update(status="filled", closed_at=at, price=price, reason=reason)
        for o in self._live(oco["ticker"]):  # nothing left to close
            if o["type"] == "close":
                o.update(status="cancelled", closed_at=at)
        return Fill(oco["id"], at, oco["ticker"], "sell", oco["qty"], round(price, 2), reason)
