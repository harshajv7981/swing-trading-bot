"""The mid-cap momentum (M6) month-end rebalance, sent to Dhan. Sandbox only; live stays read-only (R11).

The paper trial (`swingbot-momentum`, docs/research/momentum-paper-trial.md)
books each month-end's 20 picks in its ledger. This module turns the latest
booked month into real broker orders for the same rule:

    swingbot-dhan rebalance                # dry run: the orders it would send, from Dhan's live quotes
    swingbot-dhan rebalance --send         # sandbox: the sells, wait for them, then the buys
    swingbot-dhan rebalance --status       # the bot's momentum book at Dhan

How it trades:

* **Its own sub-account.** The bot only trades what its book says it owns
  (`<state_dir>/momentum_dhan_book.json`), starting from `capital` in
  config/broker.toml. Anything else in the Dhan account is never counted or sold.
* **The trial's sizing.** The book's value (cash + holdings at live prices),
  less 0.20% for costs and 0.5% for the limit band (Dhan blocks cash at the
  limit price), is split into 20 equal slots, in whole shares. Stocks
  that left the picks are sold in full; held picks are trimmed or topped up to
  their slot, as the paper ledger does. Sells go first; the buys go only once
  every sell has finished, sized from the cash the fills actually left.
* **Marketable limits, not market orders.** CNC, DAY, priced `limit_band_pct`
  (0.5%) through the last price. A gap past the band leaves the order unfilled
  and it lapses at the close; the next run plans the rest again. Outside market
  hours an order goes in as an after-market order for the open.
* **Guards** (each one refuses the whole run before anything is sent): the
  month needs exactly 20 picks; picks older than `max_age_sessions` sessions
  are stale; no buy bigger than one slot plus one share; a sell never exceeds
  what both the book and Dhan's holdings show (in the sandbox, whose
  holdings don't follow its fills, a shortfall is only noted); any order still open from an
  earlier run must finish first; and `<state_dir>/momentum_halt` stops
  everything until Harsha deletes it (`swingbot-dhan rebalance --halt "why"`).
* **R10**: every call goes through the adapter's rate limit. A rebalance is
  about 20 to 40 orders a month, far under SEBI's 10 orders a second, above
  which a retail algo needs exchange registration.
* **R11**: in live mode (config and SWINGBOT_DHAN_LIVE=yes) the dry run works on
  real quotes and real holdings, which is the shadow step before real money,
  but `--send` is refused and `DhanAPI` refuses every order call anyway.

The sandbox fills every order at a fixed price and resets its cash daily, so
the book's value there is meaningless: the sandbox tests the plumbing.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..momentum import COST_SIDE, TOP
from ..risk.calendar import NSECalendar
from ..risk.exits import nse_tick, round_down, round_up
from .base import OrderRefused
from .dhan import DEFAULT_CONFIG, DONE_STATUSES, DhanAPI, DhanError, _rows, _ts

BOOK = "momentum_dhan_book.json"
HALT = "momentum_halt"
OPEN_STATUSES = {"TRANSIT", "PENDING", "PART_TRADED", "CONFIRM"}


@dataclass(frozen=True)
class MomentumConfig:
    capital: float = 1_000_000.0          # rupees the bot may use for M6 in this account
    limit_band_pct: float = 0.5           # buy limit this % above the last price, sell limit this % below
    max_age_sessions: int = 3             # refuse picks from a month-end more sessions ago than this
    wait_seconds: int = 120               # how long --send waits for the sells before the buys
    ledger: Path = Path("data/state/momentum_trial.json")

    @classmethod
    def load(cls, path: str | Path | None = None) -> "MomentumConfig":
        path = Path(path) if path else DEFAULT_CONFIG
        raw = tomllib.loads(path.read_text()).get("momentum", {}) if path.exists() else {}
        cfg = cls(float(raw.get("capital", cls.capital)), float(raw.get("limit_band_pct", cls.limit_band_pct)),
                  int(raw.get("max_age_sessions", cls.max_age_sessions)),
                  int(raw.get("wait_seconds", cls.wait_seconds)), Path(raw.get("ledger", cls.ledger)))
        if cfg.capital <= 0 or not 0 < cfg.limit_band_pct <= 2:
            raise ValueError(f"{path}: [momentum] capital must be positive and limit_band_pct in (0, 2]")
        return cfg


def latest_picks(ledger: dict, month: str | None = None) -> tuple[str, list[str]]:
    """(month-end date, picks) of the newest booked month, or of `month` (YYYY-MM)."""
    months = [m for m in ledger.get("months", []) if not month or m["date"].startswith(month)]
    if not months:
        raise OrderRefused(f"the paper trial has no booked month{' ' + month if month else ''} yet; "
                           "run `swingbot-momentum update` first")
    m = months[-1]
    return m["date"], list(m["picks"])


def plan(picks: list[str], holdings: dict[str, int], cash: float, prices: dict[str, float],
         top: int = TOP, cost_side: float = COST_SIDE, band: float = 0.0) -> tuple[list[dict], float]:
    """The trial's equal-weight orders from the book: (orders, slot value). Sells first, then buys in pick order.

    `band` is the limit's distance through the price: Dhan blocks cash at the
    limit, so the slots leave room for it as well as for the costs."""
    missing = sorted(s for s in set(picks) | {s for s, q in holdings.items() if q} if not prices.get(s))
    if missing:
        raise OrderRefused(f"no price for {', '.join(missing)}; nothing is sent without one")
    total = cash + sum(q * prices[s] for s, q in holdings.items())
    slot = total * (1 - cost_side - band) / top
    sells, buys = [], []
    for sym, held in sorted(holdings.items()):
        want = int(slot // prices[sym]) if sym in picks else 0
        if held > want:
            sells.append({"symbol": sym, "side": "SELL", "qty": held - want, "price": prices[sym]})
    for sym in picks:
        want, held = int(slot // prices[sym]), holdings.get(sym, 0)
        if want > held:
            buys.append({"symbol": sym, "side": "BUY", "qty": want - held, "price": prices[sym]})
    return sells + buys, slot


def check(picks: list[str], orders: list[dict], slot: float, holdings: dict[str, int],
          dhan_holdings: dict[str, int] | None, top: int = TOP) -> None:
    """Refuse the whole run on anything outside the rule."""
    if len(picks) != top or len(set(picks)) != top:
        raise OrderRefused(f"the month has {len(set(picks))} distinct picks, not {top}")
    for o in orders:
        if o["side"] == "BUY":
            if o["symbol"] not in picks:
                raise OrderRefused(f"{o['symbol']} is not a pick; only picks are bought")
            have = holdings.get(o["symbol"], 0) * o["price"]
            if have + o["qty"] * o["price"] > slot + o["price"]:
                raise OrderRefused(f"buying {o['qty']} {o['symbol']} would go past one slot of Rs {slot:,.0f}")
        else:
            if o["qty"] > holdings.get(o["symbol"], 0):
                raise OrderRefused(f"selling {o['qty']} {o['symbol']}, but the bot's book holds "
                                   f"{holdings.get(o['symbol'], 0)}")
            if dhan_holdings is not None and o["qty"] > dhan_holdings.get(o["symbol"], 0):
                raise OrderRefused(f"selling {o['qty']} {o['symbol']}, but Dhan's holdings show "
                                   f"{dhan_holdings.get(o['symbol'], 0)}; check the account before trading")


class MomentumDhan:
    """The bot's M6 book at Dhan, and the month-end rebalance that trades it."""

    def __init__(self, api: DhanAPI, scrips: dict[str, str], config: MomentumConfig | None = None,
                 calendar: NSECalendar | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.api, self.scrips = api, scrips
        self.config = config or MomentumConfig()
        self.calendar = calendar or NSECalendar()
        self.clock, self.sleep = clock, sleep
        self.state_dir = api.config.state_dir
        self.path = self.state_dir / BOOK
        self.book = (json.loads(self.path.read_text()) if self.path.exists() else
                     {"capital": self.config.capital, "cash": self.config.capital, "holdings": {}, "orders": [],
                      "last_month": ""})
        self.notes: list[str] = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.book, indent=1) + "\n")
        os.replace(tmp, self.path)

    # --- the halt switch ---------------------------------------------------------

    @property
    def halt_path(self) -> Path:
        return self.state_dir / HALT

    def halt(self, why: str) -> None:
        self.halt_path.parent.mkdir(parents=True, exist_ok=True)
        self.halt_path.write_text(f"{pd.Timestamp.now(tz='Asia/Kolkata'):%Y-%m-%d %H:%M} {why}\n")

    # --- reads -------------------------------------------------------------------

    def _security(self, sym: str) -> str:
        sid = self.scrips.get(sym)
        if not sid:
            raise OrderRefused(f"{sym} is not in Dhan's scrip master as an NSE equity")
        return sid

    def quotes(self, symbols: list[str]) -> dict[str, float]:
        """Last traded prices from Dhan's market feed (POST /marketfeed/ltp, a data call)."""
        ids = {self._security(s): s for s in symbols}
        resp = self.api.call("POST", "/marketfeed/ltp", {"NSE_EQ": [int(i) for i in ids]}, with_client_id=False)
        feed = ((resp.get("data") or {}).get("NSE_EQ") or {}) if isinstance(resp, dict) else {}
        out = {}
        for sid, q in feed.items():
            price = float((q or {}).get("last_price") or 0)
            if str(sid) in ids and price > 0:
                out[ids[str(sid)]] = price
        return out

    def dhan_holdings(self) -> dict[str, int]:
        by_id = {v: k for k, v in self.scrips.items()}
        out: dict[str, int] = {}
        for r in _rows(self.api.call("GET", "/holdings")):
            sym = by_id.get(str(r.get("securityId")))
            if sym:
                out[sym] = out.get(sym, 0) + int(r.get("totalQty") or 0)
        return out

    def pending(self) -> list[dict]:
        return [o for o in self.book["orders"] if o["status"] == "open"]

    # --- fills ---------------------------------------------------------------------

    def sync(self) -> list[dict]:
        """Read every open order's status back from Dhan and move the book by what filled."""
        done = []
        for o in self.pending():
            r = self.api.call("GET", f"/orders/{o['dhan_id']}")
            r = r[0] if isinstance(r, list) and r else r
            r = r.get("data", r) if isinstance(r, dict) else {}
            status = str(r.get("orderStatus", "")).upper()
            if status in OPEN_STATUSES or not status:
                continue
            filled = int(r.get("filledQty") or (o["qty"] if status == "TRADED" else 0))
            avg = float(r.get("averageTradedPrice") or 0) or o["limit"]
            self._apply(o, filled, avg)
            o.update(status=status.lower(), filled=filled, avg_price=avg)
            if status in DONE_STATUSES and status != "TRADED":
                why = r.get("omsErrorDescription") or ""
                self.notes.append(f"{o['side'].lower()} {o['symbol']}: {status.lower()}"
                                  f"{' (' + why + ')' if why else ''}, {filled} of {o['qty']} filled")
            done.append(o)
        return done

    def _apply(self, o: dict, filled: int, price: float) -> None:
        if not filled:
            return
        h = self.book["holdings"]
        value = filled * price
        if o["side"] == "BUY":
            h[o["symbol"]] = h.get(o["symbol"], 0) + filled
            self.book["cash"] -= value * (1 + COST_SIDE)
        else:
            h[o["symbol"]] = h.get(o["symbol"], 0) - filled
            self.book["cash"] += value * (1 - COST_SIDE)
            if h[o["symbol"]] <= 0:
                del h[o["symbol"]]

    # --- orders ----------------------------------------------------------------------

    def _send(self, o: dict, month: str, at: pd.Timestamp) -> dict:
        band = self.config.limit_band_pct / 100
        tick = nse_tick(o["price"])
        limit = (round_up(o["price"] * (1 + band), tick) if o["side"] == "BUY"
                 else round_down(o["price"] * (1 - band), tick))
        minute = at.hour * 60 + at.minute
        amo = not (self.calendar.is_session(at.date()) and 9 * 60 + 15 <= minute < 15 * 60 + 30)
        oid = f"M{len(self.book['orders']) + 1:05d}"
        payload = {"correlationId": oid, "transactionType": o["side"], "exchangeSegment": "NSE_EQ",
                   "productType": "CNC", "orderType": "LIMIT", "validity": "DAY",
                   "securityId": self._security(o["symbol"]), "quantity": int(o["qty"]), "disclosedQuantity": 0,
                   "price": limit, "triggerPrice": 0.0, "afterMarketOrder": amo}
        if amo:
            payload["amoTime"] = "OPEN"
        resp = self.api.call("POST", "/orders", payload)
        rec = {"id": oid, "dhan_id": str(resp.get("orderId", "")), "month": month, "symbol": o["symbol"],
               "side": o["side"], "qty": int(o["qty"]), "ref_price": o["price"], "limit": limit,
               "sent_at": f"{at:%Y-%m-%d %H:%M}", "status": "open", "filled": 0}
        self.book["orders"].append(rec)
        self.save()   # after every order, so a crash never loses one that Dhan has
        return rec

    def _wait_for(self, side: str) -> bool:
        """Sync until no order on `side` is open, up to wait_seconds. True if they all finished."""
        deadline = self.clock() + self.config.wait_seconds
        while True:
            self.sync()
            if not any(o["side"] == side for o in self.pending()):
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(5)

    def rebalance(self, ledger: dict, at, send: bool = False, month: str | None = None,
                  prices: dict[str, float] | None = None) -> dict:
        """Plan (and with `send`, place) the month's orders. Returns what it planned and sent."""
        at = _ts(at)
        if self.halt_path.exists():
            raise OrderRefused(f"halted: {self.halt_path.read_text().strip()}. Delete {self.halt_path} to resume.")
        if send and self.api.config.live:
            raise OrderRefused("Live mode is read-only: the dry run works on the real account, --send does not.")
        self.sync()
        if self.pending():
            self.save()
            left = ", ".join(f"{o['side'].lower()} {o['symbol']}" for o in self.pending())
            raise OrderRefused(f"orders from an earlier run are still open ({left}); run again once they finish")
        me, picks = latest_picks(ledger, month)
        age = self.calendar.sessions_between(pd.Timestamp(me).date(), at.date())
        if age > self.config.max_age_sessions:
            raise OrderRefused(f"the newest picks are from {me}, {age} sessions ago (limit "
                               f"{self.config.max_age_sessions}); trading stale picks is not the rule")
        held = {s: q for s, q in self.book["holdings"].items() if q}
        prices = prices or self.quotes(sorted(set(picks) | set(held)))
        band = self.config.limit_band_pct / 100
        orders, slot = plan(picks, held, self.book["cash"], prices, band=band)
        dhan = self.dhan_holdings() if any(o["side"] == "SELL" for o in orders) else None
        if dhan is not None and not self.api.config.live:
            # The sandbox's holdings don't follow its fills reliably; there a shortfall is a note, not a refusal.
            for o in orders:
                if o["side"] == "SELL" and o["qty"] > dhan.get(o["symbol"], 0):
                    self.notes.append(f"sandbox holdings show {dhan.get(o['symbol'], 0)} {o['symbol']}, "
                                      f"the book {o['qty']}+; live mode would refuse this")
            dhan = None
        check(picks, orders, slot, held, dhan)
        out = {"month": me, "slot": slot, "planned": orders, "sent": []}
        if not send:
            return out
        for o in (o for o in orders if o["side"] == "SELL"):
            out["sent"].append(self._send(o, me, at))
        if any(o["side"] == "SELL" for o in orders) and not self._wait_for("SELL"):
            self.save()
            self.notes.append("the sells have not all finished; the buys wait for the next run")
            return out
        # Size the buys from what the sells really left in cash.
        held = {s: q for s, q in self.book["holdings"].items() if q}
        buys, slot = plan(picks, held, self.book["cash"], prices, band=band)
        buys = [o for o in buys if o["side"] == "BUY"]
        check(picks, buys, slot, held, None)
        cash = self.book["cash"]
        for o in buys:
            cost = o["qty"] * o["price"] * (1 + band) * (1 + COST_SIDE)
            if cost > cash:
                self.notes.append(f"buy {o['qty']} {o['symbol']}: Rs {cost:,.0f} needed, Rs {cash:,.0f} left; skipped")
                continue
            cash -= cost
            out["sent"].append(self._send(o, me, at))
        self.book["last_month"] = me
        self.save()
        return out


def describe(book: dict, prices: dict[str, float] | None = None) -> str:
    lines = [f"capital Rs {book['capital']:,.0f}, cash Rs {book['cash']:,.0f}, "
             f"last rebalanced for {book.get('last_month') or 'none yet'}"]
    for sym, q in sorted(book["holdings"].items()):
        p = (prices or {}).get(sym)
        lines.append(f"  {sym:<14} {q:>6}" + (f"  @ {p:,.2f} = Rs {q * p:,.0f}" if p else ""))
    open_ = [o for o in book["orders"] if o["status"] == "open"]
    if open_:
        lines.append(f"{len(open_)} order(s) open: " + ", ".join(f"{o['side'].lower()} {o['qty']} {o['symbol']}"
                                                              for o in open_))
    return "\n".join(lines)


__all__ = ["MomentumConfig", "MomentumDhan", "check", "describe", "latest_picks", "plan", "DhanError"]
