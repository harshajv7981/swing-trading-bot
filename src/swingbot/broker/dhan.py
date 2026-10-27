"""The DhanHQ adapter, for Dhan's sandbox. Live money stays off (R11).

It speaks Dhan's v2 REST API (the request and field names are the ones the
official `dhanhq` Python package sends) behind the same `Broker` interface as
the paper broker:

- submit:       POST /orders, the ticket's entry: BUY, CNC, LIMIT, DAY, with
                securityId from Dhan's scrip master. The exits are not sent with
                it: Dhan takes a Forever Order on delivery shares only from the
                next trading day.
- sync:         fills from the trade history (GET /trades/{from}/{to}/{page}),
                which, unlike the order book, survives the night. Then, for each
                position held since an earlier session, the Forever OCO (POST
                /forever/orders, both legs MARKET) and R1's check that Dhan
                lists it, with the right stop, within 60 seconds.
- cancel:       DELETE /orders/{id}, entries only.
- close:        DELETE /forever/orders/{id}, then a MARKET sell. A sell that
                Dhan rejects or lets lapse (a lower circuit, say) is dropped
                on the next sync, so the position gets its stop back.
- time exit:    a holding whose ticket's `exit_by` session has reached 15:15
                (or passed) drops its Forever OCO and sells at market.
- modify_stop:  PUT /forever/orders/{id} on the STOP_LOSS_LEG; up only, at most
                25 changes per order (Dhan's limit).
- open_orders:  the adapter's own book, `<state_dir>/dhan_book.json`.

R1, 60 seconds: a stop that Dhan does not show within 60 seconds of placing
it, or shows with the wrong trigger, is cancelled and placed once more. If the
second try fails too the position is sold at market (`flatten_unprotected`,
on by default), because a holding with no stop is what R1 forbids.

Splits and bonuses: on the ex-date the exchange cancels pending orders and the
price halves (for a 1:1 bonus). The adapter reads the same
`<state_dir>/corporate_actions.csv` as the paper broker and moves a holding's
share count, stop and target onto the new terms before placing its stop
again; the OCO covers only the shares Dhan already shows in holdings (bonus
shares arrive a few days after the ex-date) and is placed again for all of
them once they are in. A split nobody has listed yet looks like a cancelled
stop with the old numbers: the re-placed stop triggers at once or is refused,
and R1 sells, so keep the file current (`swingbot-refresh` adds to it).

Run `swingbot-dhan sync` after 09:15 on every session day (`--at-open` waits
for 09:16), because Dhan takes a Forever Order only while the market is open
and R1 can only confirm it then.

R10, order rate: every call goes through `RateLimit`, kept in
`<state_dir>/dhan_rate.json` so separate runs count together. Dhan's own
limits are 10 order calls a second, 250 a minute, 1,000 an hour and 7,000 a
day, and 5 a second for data calls; the adapter waits out a busy second or
minute and refuses past the hour and day limits.

R11, live mode: the base URL is Dhan's sandbox unless config/broker.toml says
`mode = "live"` AND the environment has `SWINGBOT_DHAN_LIVE=yes`. Even then,
this version places no order in live mode: every ticket is paper only until
the build plan's paper phase is passed, and live mode is read-only (account,
holdings, trade history).

The sandbox fills every order at a fixed price (100 at the time of writing)
and resets its cash daily, so it tests the plumbing, not the P&L: keep its
fills in their own ledger. Credentials come from the environment only:
SWINGBOT_DHAN_CLIENT_ID and SWINGBOT_DHAN_TOKEN (the sandbox token from the
DhanHQ DevPortal; a live token lasts one day and needs Dhan's static IP).
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .. import corporate
from ..models import IST
from ..risk.calendar import NSECalendar
from ..risk.config import RiskConfig
from ..risk.exits import round_down
from ..risk.portfolio import load_sectors
from .base import Fill, OrderRefused, StopMove

SANDBOX_URL = "https://sandbox.dhan.co/v2"
LIVE_URL = "https://api.dhan.co/v2"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
DEFAULT_CONFIG = Path("config/broker.toml")
DEFAULT_STATE_DIR = Path("data/state")
LIVE_ENV = "SWINGBOT_DHAN_LIVE"

CONFIRM_SECONDS = 60          # R1
TIME_EXIT_MINUTE = 15 * 60 + 15   # the time exit sells from 15:15 on its exit_by session
MAX_MODIFICATIONS = 25        # Dhan's limit per order
LIVE_STATUSES = {"PENDING", "CONFIRM"}   # accepted and waiting to trigger; TRANSIT is not yet accepted
DONE_STATUSES = {"REJECTED", "CANCELLED", "EXPIRED"}
# Trade-history charge fields that make up the contract note's costs.
CHARGES = ("brokerageCharges", "exchangeTransactionCharges", "sebiTax", "stt", "stampDuty", "serviceTax")

ORDER_LIMITS = ((1, 10), (60, 250), (3600, 1000), (86400, 7000))   # (seconds, calls) for order calls
DATA_LIMITS = ((1, 5),)


class DhanError(RuntimeError):
    """Dhan said no, or could not be reached. The message is one plain sentence."""


# --- config -------------------------------------------------------------------


@dataclass(frozen=True)
class DhanConfig:
    mode: str = "sandbox"                 # sandbox | live (R11; live also needs SWINGBOT_DHAN_LIVE=yes)
    state_dir: Path = DEFAULT_STATE_DIR
    scrip_master: Path = Path("data/state/dhan_scrips.csv")
    flatten_unprotected: bool = True      # R1: sell at market when no stop can be confirmed

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DhanConfig":
        path = Path(path) if path else DEFAULT_CONFIG
        if not path.exists():
            if path != DEFAULT_CONFIG:
                raise FileNotFoundError(f"no broker config at {path}")
            return cls()
        raw = tomllib.loads(path.read_text()).get("dhan", {})
        mode = str(raw.get("mode", "sandbox")).lower()
        if mode not in ("sandbox", "live"):
            raise ValueError(f"{path}: [dhan] mode must be sandbox or live, not {mode!r}")
        return cls(mode, Path(raw.get("state_dir", DEFAULT_STATE_DIR)),
                   Path(raw.get("scrip_master", cls.scrip_master)),
                   bool(raw.get("flatten_unprotected", True)))

    @property
    def live(self) -> bool:
        """R11. Live only when the config and the environment both say so."""
        return self.mode == "live" and os.environ.get(LIVE_ENV, "").strip().lower() == "yes"

    @property
    def base_url(self) -> str:
        if self.mode == "live" and not self.live:
            raise DhanError(f"config/broker.toml says live, but {LIVE_ENV}=yes is not set; refusing to "
                            "guess, so nothing is called. Set mode = \"sandbox\" or set the variable.")
        return LIVE_URL if self.live else SANDBOX_URL


# --- R10 ----------------------------------------------------------------------


class RateLimit:
    """Sliding windows over the calls made, persisted so separate runs count together."""

    def __init__(self, path: Path | None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.path, self.clock, self.sleep = path, clock, sleep
        self.calls: dict[str, list[float]] = {"order": [], "data": []}
        if path and path.exists():
            self.calls.update(json.loads(path.read_text()))

    def take(self, kind: str) -> None:
        limits = ORDER_LIMITS if kind == "order" else DATA_LIMITS
        while True:
            now = self.clock()
            calls = self.calls[kind] = [t for t in self.calls[kind] if now - t < 86400]
            wait = 0.0
            for window, n in limits:
                recent = [t for t in calls if now - t < window]
                if len(recent) >= n:
                    if window > 60:
                        raise DhanError(f"R10: {n} {kind} calls in the last {window // 3600} hour(s) is Dhan's "
                                        "limit; nothing more is sent until the window clears")
                    wait = max(wait, recent[0] + window - now)
            if wait <= 0:
                break
            self.sleep(wait)
        self.calls[kind].append(self.clock())
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.calls))


# --- HTTP -----------------------------------------------------------------------

Transport = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class DhanAPI:
    """Dhan's REST API with R10 on every call. `transport` is swapped out in tests."""

    def __init__(self, config: DhanConfig, client_id: str | None = None, token: str | None = None,
                 transport: Transport = urllib_transport, limit: RateLimit | None = None):
        self.config = config
        self.base = config.base_url
        self.client_id = client_id or _env("SWINGBOT_DHAN_CLIENT_ID")
        self.token = token or _env("SWINGBOT_DHAN_TOKEN")
        self.transport = transport
        self.limit = limit or RateLimit(config.state_dir / "dhan_rate.json")

    def call(self, method: str, path: str, payload: dict | None = None):
        is_order = method != "GET" and path.startswith(("/orders", "/forever"))
        if is_order and self.config.live:
            raise DhanError("Live mode is read-only in this version: no order goes to Dhan with real money "
                            "until the paper phase is passed.")
        self.limit.take("order" if is_order else "data")
        body = None
        if payload is not None:
            body = json.dumps({"dhanClientId": self.client_id, **payload}).encode()
        headers = {"access-token": self.token, "client-id": self.client_id,
                   "Content-Type": "application/json", "Accept": "application/json"}
        status, raw = self.transport(method, self.base + path, headers, body)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {"errorMessage": raw[:200].decode("utf-8", "replace")}
        if not 200 <= status < 300:
            data = data if isinstance(data, dict) else {}
            code = data.get("errorCode", "")
            hint = " (Dhan wants this machine's static IP whitelisted)" if code == "DH-905" else ""
            raise DhanError(f"Dhan {method} {path}: {status} {code} {data.get('errorMessage', '')}{hint}".strip())
        return data


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise DhanError(f"{name} is not set (the sandbox values are on the DhanHQ DevPortal's Sandbox tab)")
    return value


# --- the scrip master -----------------------------------------------------------


def parse_scrip_master(text: str) -> dict[str, str]:
    """NSE equity trading symbol -> Dhan security id, from Dhan's compact scrip master CSV."""
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        if (r.get("SEM_EXM_EXCH_ID") == "NSE" and r.get("SEM_SEGMENT") == "E"
                and r.get("SEM_SERIES", "EQ") == "EQ" and r.get("SEM_INSTRUMENT_NAME", "EQUITY") == "EQUITY"):
            out[r["SEM_TRADING_SYMBOL"].strip().upper()] = r["SEM_SMST_SECURITY_ID"].strip()
    return out


def load_scrips(path: Path) -> dict[str, str]:
    if not path.exists():
        raise DhanError(f"no scrip master at {path}; run `swingbot-dhan scrips --refresh` once")
    with path.open(newline="") as fh:
        return {r["symbol"]: r["security_id"] for r in csv.DictReader(fh)}


def save_scrips(scrips: dict[str, str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["symbol", "security_id"])
        w.writerows(sorted(scrips.items()))


# --- the broker -----------------------------------------------------------------


def _ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def _rows(data) -> list[dict]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data", []) if isinstance(data.get("data"), list) else []
    return []


OPEN_SYNC = (9, 16)   # a minute after the open, so Dhan takes the Forever Orders


def wait_for_open(now: pd.Timestamp, calendar: NSECalendar) -> float:
    """Seconds until 09:16 IST today if that is still ahead on a session day, else 0."""
    now = _ts(now)
    at = now.normalize() + pd.Timedelta(hours=OPEN_SYNC[0], minutes=OPEN_SYNC[1])
    if not calendar.is_session(now.date()) or now >= at:
        return 0.0
    return (at - now).total_seconds()


class DhanBroker:
    """Same methods as `PaperBroker`. Save only after the fills from `sync` are in the ledger."""

    def __init__(self, api: DhanAPI, risk: RiskConfig | None = None, calendar: NSECalendar | None = None,
                 scrips: dict[str, str] | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, actions: dict | None = None):
        self.api = api
        self.config = api.config
        self.risk = risk or RiskConfig()
        self.calendar = calendar or NSECalendar()
        self._scrips = scrips
        self.clock, self.sleep = clock, sleep
        # Splits and bonuses, as the paper broker reads them (swingbot.corporate).
        self.actions = corporate.load(self.config.state_dir) if actions is None else actions
        self.path = self.config.state_dir / "dhan_book.json"
        state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.orders: list[dict] = state.get("orders", [])
        self.seen_trades: list[str] = state.get("seen_trades", [])
        self.notes: list[str] = []

    @property
    def scrips(self) -> dict[str, str]:
        if self._scrips is None:
            self._scrips = load_scrips(self.config.scrip_master)
        return self._scrips

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"orders": self.orders, "seen_trades": self.seen_trades}, indent=2) + "\n")
        os.replace(tmp, self.path)

    def open_orders(self) -> list[dict]:
        return [o for o in self.orders if o["status"] in ("open", "held")]

    def _held(self, ticker: str) -> dict | None:
        return next((o for o in self.orders if o["ticker"] == ticker and o["status"] == "held"), None)

    def _next_session(self, at: pd.Timestamp) -> date:
        day = at.tz_localize(None).date()
        if at.hour * 60 + at.minute >= 15 * 60 + 30:
            day += timedelta(days=1)
        while not self.calendar.is_session(day):
            day += timedelta(days=1)
        return day

    # --- entries -----------------------------------------------------------------

    def submit(self, ticket: dict, at, kill_switch_latched: bool = False) -> str:
        if kill_switch_latched:
            raise OrderRefused("The weekly kill switch is latched, so no new entry goes in until Harsha resets it.")
        if ticket.get("paper_only") is not True:
            raise OrderRefused("The ticket is not marked paper_only; this adapter only takes paper tickets.")
        e, x = ticket["entry"], ticket["exits"]
        ticker = e["tradingSymbol"].upper()
        if e["transactionType"] != "BUY" or e["orderType"] != "LIMIT" or e["productType"] != "CNC":
            raise OrderRefused("The entry must be a CNC limit buy.")
        if x.get("orderFlag") != "OCO":
            raise OrderRefused("The exits must be one OCO pair.")
        if any(o["ticker"] == ticker for o in self.open_orders()):
            raise OrderRefused(f"{ticker} already has a live order or position here; the bot does not add.")
        if not ticket.get("checked_at") or not ticket.get("fingerprint"):
            raise OrderRefused("The ticket has no PASS time or fingerprint from the risk gate.")
        at = _ts(at)
        session = self._next_session(at)
        if self.calendar.sessions_between(_ts(ticket["checked_at"]).date(), session) > 1:
            raise OrderRefused(f"The PASS is from {ticket['checked_at'][:10]}; the entry would go in on {session}.")
        security = self.scrips.get(ticker)
        if not security:
            raise OrderRefused(f"{ticker} is not in Dhan's scrip master as an NSE equity.")
        qty, limit = int(e["quantity"]), float(e["price"])
        stop, target = float(x["stop"]["triggerPrice"]), float(x["target"]["triggerPrice"])
        if qty <= 0 or not stop < limit < target:
            raise OrderRefused(f"{ticker}: the ticket needs a positive quantity and stop < entry < target.")
        oid = f"D{len(self.orders) + 1:05d}"
        # Placed outside market hours, it waits as an after-market order for the open.
        amo = not (9 * 60 + 15 <= at.hour * 60 + at.minute < 15 * 60 + 30 and session == at.date())
        payload = {"correlationId": oid, "transactionType": "BUY", "exchangeSegment": "NSE_EQ",
                   "productType": "CNC", "orderType": "LIMIT", "validity": "DAY", "securityId": security,
                   "quantity": qty, "disclosedQuantity": 0, "price": limit, "triggerPrice": 0.0,
                   "afterMarketOrder": amo}
        if amo:
            payload["amoTime"] = "OPEN"
        resp = self.api.call("POST", "/orders", payload)
        self.orders.append({
            "id": oid, "dhan_id": str(resp.get("orderId", "")), "type": "entry", "status": "open",
            "ticker": ticker, "security_id": security, "qty": qty, "limit": limit, "stop": stop, "target": target,
            "session": session.isoformat(), "submitted_at": f"{at:%Y-%m-%d %H:%M}",
            "fingerprint": ticket["fingerprint"], "checked_at": ticket["checked_at"],
            "sector": ticket.get("sector") or load_sectors().get(ticker, ""), "filled": 0, "forever_id": "",
            "modifications": 0, "exit_by": (ticket.get("hold") or {}).get("exit_by"),
        })
        return oid

    def cancel(self, order_id: str, at) -> None:
        o = next((o for o in self.orders if o["id"] == order_id), None)
        if o is None or o["status"] != "open":
            raise OrderRefused(f"No live entry {order_id}.")
        self.api.call("DELETE", f"/orders/{o['dhan_id']}")
        o.update(status="cancelled", closed_at=f"{_ts(at):%Y-%m-%d %H:%M}")

    # --- exits -------------------------------------------------------------------

    def close(self, ticker: str, at) -> str:
        """Drop the Forever OCO and sell the holding at market."""
        return self._close(ticker, "close")

    def _close(self, ticker: str, why: str) -> str:
        o = self._held(ticker.upper())
        if o is None:
            raise OrderRefused(f"{ticker} is not held here.")
        if o["forever_id"]:
            self.api.call("DELETE", f"/forever/orders/{o['forever_id']}")
            o["forever_id"] = ""
        return self._sell(o, why)

    def _left(self, o: dict) -> int:
        """Shares the book still holds."""
        return o["filled"] - o.get("sold", 0)

    def _at_dhan(self, o: dict) -> int:
        """Shares an exit can cover: all of them, except after a bonus whose shares Dhan hasn't credited yet."""
        if not o.get("actions"):
            return self._left(o)
        held = sum(int(r.get("totalQty") or 0) for r in _rows(self.api.call("GET", "/holdings"))
                   if str(r.get("securityId")) == o["security_id"])
        return min(self._left(o), held)

    def _sell(self, o: dict, why: str) -> str:
        qty = self._at_dhan(o)
        resp = self.api.call("POST", "/orders", {
            "correlationId": f"{o['id']}X", "transactionType": "SELL", "exchangeSegment": "NSE_EQ",
            "productType": "CNC", "orderType": "MARKET", "validity": "DAY", "securityId": o["security_id"],
            "quantity": qty, "disclosedQuantity": 0, "price": 0.0, "triggerPrice": 0.0,
            "afterMarketOrder": False})
        o["exit_id"], o["exit_reason"] = str(resp.get("orderId", "")), why
        return o["exit_id"]

    def modify_stop(self, ticker: str, stop: float, at, reason: str = "moved by hand") -> StopMove:
        o = self._held(ticker.upper())
        if o is None or not o["forever_id"]:
            raise OrderRefused(f"{ticker} has no live stop at Dhan.")
        if stop < o["stop"]:
            raise OrderRefused(f"{ticker}: the stop only moves up (now {o['stop']:,.2f}, asked {stop:,.2f}).")
        if stop >= o["target"]:
            raise OrderRefused(f"{ticker}: a stop at or above the target {o['target']:,.2f} makes no sense.")
        if o["modifications"] >= MAX_MODIFICATIONS:
            raise OrderRefused(f"{ticker}: Dhan allows {MAX_MODIFICATIONS} changes per order; this one has used them.")
        self.api.call("PUT", f"/forever/orders/{o['forever_id']}", {
            "orderId": o["forever_id"], "orderFlag": "OCO", "orderType": "MARKET", "legName": "STOP_LOSS_LEG",
            "quantity": o.get("covered") or self._left(o), "price": 0.0, "triggerPrice": stop,
            "disclosedQuantity": 0, "validity": "DAY"})
        o["modifications"] += 1
        o["stop"] = stop
        return StopMove(o["id"], f"{_ts(at):%Y-%m-%d %H:%M}", o["ticker"], stop, reason)

    def _place_oco(self, o: dict, qty: int) -> str:
        # First leg (price, triggerPrice) is the TARGET_LEG, second (price1, triggerPrice1)
        # the STOP_LOSS_LEG, per Dhan's modify docs. R1's read-back checks it either way.
        resp = self.api.call("POST", "/forever/orders", {
            "correlationId": f"{o['id']}F", "orderFlag": "OCO", "transactionType": "SELL",
            "exchangeSegment": "NSE_EQ", "productType": "CNC", "orderType": "MARKET", "validity": "DAY",
            "securityId": o["security_id"], "quantity": qty, "disclosedQuantity": 0,
            "price": 0.0, "triggerPrice": o["target"], "price1": 0.0, "triggerPrice1": o["stop"],
            "quantity1": qty})
        return str(resp.get("orderId", ""))

    def _confirmed(self, o: dict, forever_id: str) -> str:
        """R1: poll Dhan's Forever book for up to 60 seconds. "" when the stop is live and right."""
        deadline = self.clock() + CONFIRM_SECONDS
        problem = "Dhan did not list it"
        while True:
            legs = [r for r in _rows(self.api.call("GET", "/forever/orders"))
                    if str(r.get("orderId")) == forever_id]
            stop_legs = [r for r in legs if r.get("legName") == "STOP_LOSS_LEG"]
            if any(r.get("orderStatus") in DONE_STATUSES for r in legs):
                return f"Dhan shows it {legs[0].get('orderStatus')}"
            if stop_legs and stop_legs[0].get("orderStatus") in LIVE_STATUSES:
                trigger = float(stop_legs[0].get("triggerPrice") or 0)
                if abs(trigger - o["stop"]) < 0.005:
                    return ""
                return f"Dhan's stop leg triggers at {trigger:,.2f}, not {o['stop']:,.2f}"
            if self.clock() >= deadline:
                return problem + f" within {CONFIRM_SECONDS} seconds"
            self.sleep(5)

    def _protect(self, o: dict) -> None:
        qty = self._at_dhan(o)
        if qty <= 0:
            self.notes.append(f"{o['ticker']}: Dhan shows none of the {self._left(o)} shares in holdings, so no "
                              "stop can go in yet; check the account by hand (R1)")
            return
        for attempt in (1, 2):
            fid = self._place_oco(o, qty)
            problem = self._confirmed(o, fid)
            if not problem:
                o.update(forever_id=fid, covered=qty,
                         protected_at=pd.Timestamp.now(IST).strftime("%Y-%m-%d %H:%M:%S"))
                short = self._left(o) - qty
                self.notes.append(f"{o['ticker']}: stop {o['stop']:,.2f} and target {o['target']:,.2f} "
                                  f"confirmed at Dhan (R1)" + (f"; it covers {qty} shares, and the other {short} "
                                                               "get it once Dhan credits them" if short else ""))
                return
            try:
                self.api.call("DELETE", f"/forever/orders/{fid}")
            except DhanError:
                pass
            self.notes.append(f"{o['ticker']}: try {attempt} at the stop failed: {problem}")
        if self.config.flatten_unprotected:
            self._sell(o, "unprotected")
            self.notes.append(f"{o['ticker']} has NO confirmed stop at Dhan, so it was sold at market (R1)")
        else:
            self.notes.append(f"{o['ticker']} has NO confirmed stop at Dhan; sell it or place the stop by hand (R1)")

    # --- settling ------------------------------------------------------------------

    def _trades(self, since: date, until: date) -> list[dict]:
        """The trade history (earlier days) plus today's trade book; the same trade may be in both."""
        out, page = [], 0
        while page < 50:
            rows = _rows(self.api.call("GET", f"/trades/{since:%Y-%m-%d}/{until:%Y-%m-%d}/{page}"))
            if not rows:
                break
            out += rows
            page += 1
        return out + _rows(self.api.call("GET", "/trades"))

    def sync(self, as_of, bars: dict | None = None) -> list[Fill | StopMove]:
        """Fills since the last sync from Dhan's trades, then R1 for positions due a stop.

        `bars` is ignored: Dhan's own trades are the record. Run it after 09:15
        on a session day so the Forever Orders can go in.
        """
        as_of = _ts(as_of)
        mine = [o for o in self.orders if o["status"] in ("open", "held")]
        if not mine:
            return []
        self._corporate(as_of.date().isoformat())
        since = min(date.fromisoformat(o["session"]) for o in mine) - timedelta(days=1)
        buys: dict[str, list[dict]] = {}
        sells: dict[str, list[dict]] = {}   # by security id: a triggered Forever leg gets an order id of its own
        for t in self._trades(since, as_of.date()):
            tid = f"{t.get('orderId')}:{t.get('exchangeTradeId', '')}"
            if tid in self.seen_trades:
                continue
            self.seen_trades.append(tid)
            if str(t.get("transactionType", "")).upper() == "BUY":
                buys.setdefault(str(t.get("orderId")), []).append(t)
            else:
                sells.setdefault(str(t.get("securityId")), []).append(t)
        events: list[Fill | StopMove] = []
        for o in mine:
            events += self._fills(o, buys.get(o["dhan_id"], []), "buy")
            if o["status"] == "held" or o["filled"]:
                events += self._fills(o, sells.pop(o["security_id"], []), "sell")
            if o["status"] == "open" and as_of.date().isoformat() > o["session"]:
                if o["filled"]:
                    o["status"] = "held"
                else:
                    o.update(status="expired", closed_at=o["session"])
                    self.notes.append(f"{o['id']} {o['ticker']}: no fill on {o['session']}, so it lapsed")
        for sec, ts in sells.items():
            self.notes.append(f"a sell of {sum(int(t.get('tradedQuantity', 0)) for t in ts)} of security {sec} "
                              "at Dhan matches no position here; check it by hand")
        self._check_exits()
        self._check_stops()
        self._time_exits(as_of)   # first, so a position due out is not given a fresh stop
        session_open = self.calendar.is_session(as_of.date()) and as_of.hour * 60 + as_of.minute >= 9 * 60 + 15
        for o in self.orders:
            due = (o["status"] == "held" and not o["forever_id"] and not o.get("exit_id")
                   and as_of.date().isoformat() > o["session"])
            if due and session_open:
                self._protect(o)
            elif due:
                self.notes.append(f"{o['ticker']} has no stop at Dhan yet; sync again after 09:15 to place it")
        return events

    def _time_exits(self, as_of: pd.Timestamp) -> None:
        """Sell holdings whose time exit has come: from 15:15 on the exit_by session, or any later session."""
        today, minute = as_of.date().isoformat(), as_of.hour * 60 + as_of.minute
        trading = self.calendar.is_session(as_of.date()) and 9 * 60 + 15 <= minute < 15 * 60 + 30
        for o in self.orders:
            if o["status"] != "held" or o.get("exit_id") or not o.get("exit_by") or today < o["exit_by"]:
                continue
            if trading and (today > o["exit_by"] or minute >= TIME_EXIT_MINUTE):
                self._close(o["ticker"], "time")
                self.notes.append(f"{o['ticker']}: time exit ({o['exit_by']}), sold at market")
            elif today > o["exit_by"] or minute >= 15 * 60 + 30:
                self.notes.append(f"{o['ticker']} is past its time exit {o['exit_by']}; sync during market "
                                  "hours to sell it")

    def _corporate(self, d: str) -> None:
        """On a split or bonus ex-date, move holdings and entries onto the new terms (see the module notes)."""
        tick = self.risk.tick
        for o in self.open_orders():
            for a in self.actions.get(o["ticker"], []):
                ex = a.ex_date.isoformat()
                if ex > d or ex in o.get("actions", []) or o.get("submitted_at", "") >= a.noted:
                    continue
                r = a.ratio
                for k in ("qty", "filled", "sold"):
                    if o.get(k):
                        o[k] = corporate.shares(o[k], r)
                o["limit"] = round(o["limit"] / r, 2)
                o["stop"], o["target"] = round_down(o["stop"] / r, tick), round_down(o["target"] / r, tick)
                o["actions"] = o.get("actions", []) + [ex]
                if o["forever_id"]:
                    o["replace_stop"] = True   # whatever Dhan still shows is priced for the old shares
                self.notes.append(f"{o['id']} {o['ticker']}: {r:g}-for-1 split or bonus on {ex}; now "
                                  f"{o['filled'] or o['qty']} shares, stop {o['stop']:,.2f}")

    def _check_exits(self) -> None:
        """A market sell Dhan rejected or let lapse leaves the shares held: forget it, so R1 protects them again."""
        for o in self.orders:
            if o["status"] != "held" or not o.get("exit_id"):
                continue
            data = self.api.call("GET", f"/orders/{o['exit_id']}")
            rows = [data] if isinstance(data, dict) and "orderStatus" in data else _rows(data)
            state = rows[0] if rows else {}
            status = str(state.get("orderStatus", "")).upper()
            if status in DONE_STATUSES:
                why = state.get("omsErrorDescription") or "no reason given"
                self.notes.append(f"{o['ticker']}: the {o['exit_reason']} sell was {status} at Dhan ({why}) and the "
                                  "shares are still held; they get a stop again (R1)")
                o.pop("exit_id")
                o.pop("exit_reason")

    def _check_stops(self) -> None:
        """A held position whose Forever OCO is gone at Dhan, with no sell, has no stop: place it again.

        So is one whose stop is priced for the shares before a split or bonus,
        and one that covers fewer shares than Dhan now holds.
        """
        held = [o for o in self.orders if o["status"] == "held" and o["forever_id"] and not o.get("exit_id")]
        if not held:
            return
        book = _rows(self.api.call("GET", "/forever/orders"))
        for o in held:
            legs = [r for r in book if str(r.get("orderId")) == o["forever_id"]]
            gone = not legs or all(r.get("orderStatus") in DONE_STATUSES for r in legs)
            if gone:
                state = legs[0].get("orderStatus") if legs else "missing"
                self.notes.append(f"{o['ticker']}: the stop at Dhan is {state} and the shares are still held; "
                                  "placing it again (R1)")
            elif o.get("replace_stop"):
                self.notes.append(f"{o['ticker']}: the stop at Dhan is still priced for the old shares; "
                                  "cancelling it and placing the new one (R1)")
            elif o.get("covered", self._left(o)) < self._left(o) and self._at_dhan(o) > o["covered"]:
                self.notes.append(f"{o['ticker']}: Dhan has credited more shares; placing the stop for all of "
                                  "them (R1)")
            else:
                continue
            if not gone:
                try:
                    self.api.call("DELETE", f"/forever/orders/{o['forever_id']}")
                except DhanError as e:
                    # Two live stops could sell the shares twice: keep the old one and try next sync.
                    self.notes.append(f"{o['ticker']}: could not cancel the old stop ({e}); it stays, and the "
                                      "new one goes in on the next sync")
                    continue
            o.pop("replace_stop", None)
            o["forever_id"] = ""

    def _fills(self, o: dict, trades: list[dict], side: str) -> list[Fill]:
        out = []
        for t in trades:
            qty, price = int(t.get("tradedQuantity", 0)), float(t.get("tradedPrice", 0))
            at = str(t.get("exchangeTime") or t.get("createTime") or "")[:16]
            costs = sum(float(t.get(k) or 0) for k in CHARGES) if any(k in t for k in CHARGES) else None
            if side == "buy":
                o["filled"] += qty
                out.append(Fill(o["id"], at, o["ticker"], "buy", qty, price, "entry", o["stop"], o["target"],
                                o["sector"], o["fingerprint"], "PASS", costs))
                continue
            if o.get("exit_id") and str(t.get("orderId")) == o["exit_id"]:
                reason = o["exit_reason"]
            elif price <= (o["stop"] + o["target"]) / 2:
                reason = "stop-gap" if price < o["stop"] * 0.99 else "stop"
            else:
                reason = "target"
            out.append(Fill(o["id"], at, o["ticker"], "sell", qty, price, reason, costs=costs))
            o["sold"] = o.get("sold", 0) + qty
            if o.get("exit_id") and str(t.get("orderId")) == o["exit_id"] and o["sold"] < o["filled"]:
                o.pop("exit_id")   # it sold what Dhan held; bonus shares credited later get a stop of their own
            if o["sold"] >= o["filled"]:
                o.update(status="closed", closed_at=at)
                if o.get("forever_id") and reason in ("close", "unprotected"):
                    o["forever_id"] = ""
        return out
