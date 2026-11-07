"""The Alpaca adapter for US equities: paper account only. Live is refused in code.

It speaks Alpaca's Trading API v2 behind the normalized contracts in
swingbot.markets (OrderIntent in; OrderUpdate, Fill and Position out):

- submit:     POST /v2/orders with our client_order_id, which Alpaca keeps
              unique per account, so a retry can never place a second order.
              The intent is written to the book before the request goes out.
              If the response is lost, the order is looked up by client id
              (GET /v2/orders:by_client_order_id) and adopted; only when
              Alpaca has never seen it is it sent once more.
- cancel:     DELETE /v2/orders/{id}, open orders only.
- close:      a market sell of the whole holding the book's fills show.
- sync:       each open order's status (GET /v2/orders/{id}), then fills
              from GET /v2/account/activities/FILL, paged, each activity id
              reported once (kept in the book, so a restart does not repeat
              them). Nothing is saved unless the whole sync parses.
- positions:  GET /v2/positions; `reconcile` compares them with the book.

Fail closed: a response missing a field, a status nobody mapped, a
fractional share, or an order whose status or filled quantity goes
backwards raises AlpacaError and leaves the book as it was.

What must be true before anything is sent:

1. `enabled = true` under [alpaca] in config/broker.toml. Keys alone are not enough.
2. Credentials in the environment: SWINGBOT_ALPACA_KEY_ID and SWINGBOT_ALPACA_SECRET_KEY.
3. Mode paper. Shadow reads the account and sends nothing; live raises at construction.
4. No halt file (data/state/markets/HALT or data/state/markets/us/HALT).

Every response is appended, raw, to data/state/markets/us/alpaca_raw.jsonl
(credentials are never written). Bracket and OCO orders are not used yet:
the plan allows them only after they are checked against the internal
OCO/stop contract. Fees are booked as 0.0: Alpaca charges no commission, and
regulatory fees on sells arrive as separate account activities that this
version does not read yet.

Field names come from Alpaca's API reference. The adapter has only been run
against the fake in tests/alpaca_fake.py, not the real paper account.
"""

from __future__ import annotations

import copy
import json
import os
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from ..markets.adapter import AdapterRefused, net_positions
from ..markets.contracts import (
    ContractError,
    Fill,
    OrderIntent,
    OrderStatus,
    OrderUpdate,
    Position,
)
from ..markets.control import (
    STATE_ROOT,
    ExecutionMode,
    SendRefused,
    may_send,
    utc_stamp,
)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"   # never used by this version
DEFAULT_CONFIG = Path("config/broker.toml")
KEY_ENV, SECRET_ENV = "SWINGBOT_ALPACA_KEY_ID", "SWINGBOT_ALPACA_SECRET_KEY"
MARKET = "us"

# Alpaca order status -> ours. Statuses where the order still rests at the
# broker map to ACCEPTED (PARTIALLY_FILLED once something has filled).
STATUS = {
    "new": OrderStatus.ACCEPTED, "accepted": OrderStatus.ACCEPTED, "pending_new": OrderStatus.ACCEPTED,
    "accepted_for_bidding": OrderStatus.ACCEPTED, "pending_cancel": OrderStatus.ACCEPTED,
    "pending_replace": OrderStatus.ACCEPTED, "calculated": OrderStatus.ACCEPTED, "stopped": OrderStatus.ACCEPTED,
    "held": OrderStatus.ACCEPTED, "done_for_day": OrderStatus.ACCEPTED, "suspended": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED, "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED, "replaced": OrderStatus.CANCELLED,
    "expired": OrderStatus.EXPIRED, "rejected": OrderStatus.REJECTED,
}
_RANK = {OrderStatus.NEW: 0, OrderStatus.ACCEPTED: 1, OrderStatus.PARTIALLY_FILLED: 2}


class AlpacaError(RuntimeError):
    """Alpaca said something we cannot use, or could not be reached. One plain sentence."""


class AlpacaUnreachable(AlpacaError):
    """No usable answer: the request may or may not have reached Alpaca."""


class AlpacaRejected(AlpacaError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Alpaca refused it ({status}): {message}")
        self.status, self.message = status, message


# --- config ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlpacaConfig:
    enabled: bool = False
    mode: ExecutionMode = ExecutionMode.PAPER
    state_root: Path = STATE_ROOT

    @property
    def state_dir(self) -> Path:
        return Path(self.state_root) / "markets" / MARKET

    @property
    def base_url(self) -> str:
        if self.mode is ExecutionMode.LIVE:
            raise SendRefused("live Alpaca orders are blocked in code until the paper and shadow gates pass "
                              "and a separate change unlocks them.")
        return PAPER_URL

    @classmethod
    def load(cls, path: str | Path | None = None, required: bool = True) -> AlpacaConfig:
        path = Path(path) if path else DEFAULT_CONFIG
        if not path.exists():
            if required and path != DEFAULT_CONFIG:
                raise FileNotFoundError(f"no broker config at {path}")
            return cls()
        raw = tomllib.loads(path.read_text()).get("alpaca", {})
        return cls(bool(raw.get("enabled", False)), ExecutionMode.parse(str(raw.get("mode", "paper"))),
                   Path(raw.get("state_root", STATE_ROOT)))


# --- HTTP -------------------------------------------------------------------------------

Transport = Callable[[str, str, dict, bytes | None], tuple[int, bytes]]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class AlpacaAPI:
    def __init__(self, config: AlpacaConfig, key_id: str, secret: str, transport: Transport = urllib_transport):
        self.base_url = config.base_url            # raises for live before anything else
        if not config.enabled:
            raise AlpacaError("the Alpaca adapter is not enabled: set enabled = true under [alpaca] in "
                              "config/broker.toml. Keys alone send nothing.")
        self.config, self.transport = config, transport
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret,
                         "Content-Type": "application/json", "Accept": "application/json"}
        self.journal = config.state_dir / "alpaca_raw.jsonl"

    @classmethod
    def from_env(cls, config: AlpacaConfig, transport: Transport = urllib_transport) -> AlpacaAPI:
        if not config.enabled or config.mode is ExecutionMode.LIVE:
            return cls(config, "", "", transport)    # raises the live or not-enabled error
        creds = []
        for name in (KEY_ENV, SECRET_ENV):
            value = os.environ.get(name, "").strip()
            if not value:
                raise AlpacaError(f"{name} is not set; Alpaca credentials come from the environment only.")
            creds.append(value)
        return cls(config, *creds, transport=transport)

    def call(self, method: str, path: str, payload: dict | None = None, query: dict | None = None,
             allow_404: bool = False):
        if method != "GET":
            may_send(MARKET, self.config.mode, self.config.state_root)
        url = self.base_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            status, raw = self.transport(method, url, dict(self._headers), body)
        except OSError as e:
            self._journal(method, path, payload or query, None, str(e))
            raise AlpacaUnreachable(f"no answer from Alpaca for {method} {path}: {e}") from e
        text = raw.decode("utf-8", "replace")
        self._journal(method, path, payload or query, status, text)
        if status == 404 and allow_404:
            return None
        if status >= 500:
            raise AlpacaUnreachable(f"Alpaca answered {status} for {method} {path}")
        if status >= 400:
            try:
                message = json.loads(text).get("message", text)
            except (ValueError, AttributeError):
                message = text
            raise AlpacaRejected(status, str(message))
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as e:
            raise AlpacaError(f"malformed response from Alpaca for {method} {path}: not JSON") from e

    def _journal(self, method, path, sent, status, body) -> None:
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at_utc": utc_stamp(datetime.now(UTC)), "method": method, "path": path, "sent": sent,
               "status": status, "body": body}
        with self.journal.open("a") as f:
            f.write(json.dumps(rec) + "\n")


# --- parsing (fail closed) ------------------------------------------------------------------


def _ts(value) -> datetime:
    try:
        t = pd.Timestamp(value)
    except (ValueError, TypeError) as e:
        raise AlpacaError(f"malformed timestamp from Alpaca: {value!r}") from e
    if t is pd.NaT or t.tzinfo is None:
        raise AlpacaError(f"malformed timestamp from Alpaca: {value!r}")
    return t.to_pydatetime(warn=False)


def _whole(value, what: str) -> int:
    try:
        x = float(value)
    except (TypeError, ValueError) as e:
        raise AlpacaError(f"malformed {what} from Alpaca: {value!r}") from e
    if x != int(x):
        raise AlpacaError(f"{what} {value} is not a whole share; this version trades whole shares only")
    return int(x)


def _order(body, cid: str) -> dict:
    if not isinstance(body, dict):
        raise AlpacaError("malformed order response from Alpaca: not an object")
    missing = [k for k in ("id", "client_order_id", "status", "filled_qty") if body.get(k) in (None, "")]
    if missing:
        raise AlpacaError(f"malformed order response from Alpaca: missing {', '.join(missing)}")
    if body["client_order_id"] != cid:
        raise AlpacaError(f"malformed order response from Alpaca: asked for {cid}, got {body['client_order_id']}")
    if body["status"] not in STATUS:
        raise AlpacaError(f"Alpaca sent an order status nobody mapped: {body['status']!r}; nothing was changed")
    filled = _whole(body["filled_qty"], "filled quantity")
    status = STATUS[body["status"]]
    if status is OrderStatus.ACCEPTED and filled > 0:
        status = OrderStatus.PARTIALLY_FILLED
    at = _ts(body["updated_at"]) if body.get("updated_at") else datetime.now(UTC)
    return {"broker_order_id": str(body["id"]), "status": status.value, "filled_qty": filled,
            "updated_utc": utc_stamp(at)}


def _check_forward(cid: str, old: dict, new: dict) -> None:
    """A stale answer must not move an order backwards."""
    before, after = OrderStatus(old["status"]), OrderStatus(new["status"])
    if new["filled_qty"] < old["filled_qty"] or (before.final and after is not before) or (
            not after.final and _RANK.get(after, 0) < _RANK.get(before, 0)):
        raise AlpacaError(f"Alpaca's answer for {cid} goes backwards ({before.value}, {old['filled_qty']} filled -> "
                          f"{after.value}, {new['filled_qty']} filled); treating it as stale, nothing was changed")


# --- the broker ---------------------------------------------------------------------------------


class AlpacaBroker:
    market = MARKET
    page_size = 100

    def __init__(self, api: AlpacaAPI, state_dir: Path | None = None):
        self.api = api
        self.state_dir = Path(state_dir) if state_dir else api.config.state_dir
        self.path = self.state_dir / "alpaca_book.json"
        self.notes: list[str] = []
        self._book = (json.loads(self.path.read_text()) if self.path.exists()
                      else {"orders": {}, "fills": [], "fills_seen": []})

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._book, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def _update(self, cid: str, orders: dict | None = None) -> OrderUpdate:
        o = (orders or self._book["orders"])[cid]
        return OrderUpdate(MARKET, cid, o["broker_order_id"], OrderStatus(o["status"]), o["filled_qty"],
                           datetime.fromisoformat(o["updated_utc"]), o.get("reason", ""))

    def _fills(self) -> list[Fill]:
        out = []
        for r in self._book["fills"]:
            r = {k: v for k, v in r.items() if k not in ("kind", "currency")}
            out.append(Fill(**{**r, "at_utc": datetime.fromisoformat(r["at_utc"])}))
        return out

    # --- orders --------------------------------------------------------------------------

    def submit(self, intent: OrderIntent) -> OrderUpdate:
        if intent.market != MARKET:
            raise AdapterRefused(f"the Alpaca adapter trades us only; {intent.market} orders go to its own adapter.")
        cid = intent.client_order_id
        if cid in self._book["orders"]:
            return self._update(cid)
        may_send(MARKET, self.api.config.mode, self.api.config.state_root)
        self._book["orders"][cid] = {"intent": intent.to_record(), "broker_order_id": "", "status": "new",
                                     "filled_qty": 0, "reported": "new", "reason": "",
                                     "updated_utc": utc_stamp(intent.created_utc)}
        self._save()                                       # write-ahead: the intent is on disk before it goes
        payload = {"symbol": intent.symbol, "qty": str(intent.qty), "side": intent.side,
                   "type": intent.order_type, "time_in_force": intent.time_in_force, "client_order_id": cid}
        if intent.limit_price is not None:
            payload["limit_price"] = f"{intent.limit_price:g}"
        if intent.stop_price is not None:
            payload["stop_price"] = f"{intent.stop_price:g}"
        try:
            try:
                body = self.api.call("POST", "/v2/orders", payload)
            except AlpacaUnreachable:
                body = self._lookup(cid)
                if body is None:                           # it never arrived: send it once more
                    body = self.api.call("POST", "/v2/orders", payload)
        except AlpacaRejected as e:
            o = self._book["orders"][cid]
            o.update(status=OrderStatus.REJECTED.value, reported=OrderStatus.REJECTED.value, reason=e.message,
                     updated_utc=utc_stamp(datetime.now(UTC)))
            self._save()
            return self._update(cid)
        parsed = _order(body, cid)
        self._book["orders"][cid].update(parsed, reported=parsed["status"])
        self._save()
        return self._update(cid)

    def _lookup(self, cid: str):
        return self.api.call("GET", "/v2/orders:by_client_order_id", query={"client_order_id": cid}, allow_404=True)

    def cancel(self, client_order_id: str) -> OrderUpdate:
        o = self._book["orders"].get(client_order_id)
        if o is None:
            raise AdapterRefused(f"no order {client_order_id} in this book.")
        if OrderStatus(o["status"]).final:
            raise AdapterRefused(f"{client_order_id} is already {o['status']}.")
        if not o["broker_order_id"]:
            raise AdapterRefused(f"{client_order_id} has no Alpaca id yet; run sync first.")
        try:
            self.api.call("DELETE", f"/v2/orders/{o['broker_order_id']}")
        except AlpacaRejected as e:
            raise AdapterRefused(f"Alpaca would not cancel {client_order_id}: {e.message}") from e
        parsed = _order(self.api.call("GET", f"/v2/orders/{o['broker_order_id']}"), client_order_id)
        _check_forward(client_order_id, o, parsed)
        o.update(parsed, reported=parsed["status"])
        self._save()
        return self._update(client_order_id)

    def close(self, symbol: str, at: datetime) -> OrderUpdate:
        held = {p.symbol: p.qty for p in net_positions(MARKET, self._fills())}.get(symbol, 0)
        if held <= 0:
            raise AdapterRefused(f"no {symbol} held in this book.")
        cid = f"us-close-{symbol}-{at.astimezone(UTC):%Y%m%dT%H%M%S}"
        return self.submit(OrderIntent(MARKET, cid, symbol, "sell", held, "market", "day", at, strategy="close"))

    # --- sync ----------------------------------------------------------------------------------

    def sync(self) -> list[OrderUpdate | Fill]:
        orders = copy.deepcopy(self._book["orders"])
        for cid, o in orders.items():
            if OrderStatus(o["status"]).final:
                continue
            if o["broker_order_id"]:
                body = self.api.call("GET", f"/v2/orders/{o['broker_order_id']}")
            else:
                body = self._lookup(cid)
                if body is None:
                    self.notes.append(f"{cid} never reached Alpaca; it stays new until sent again.")
                    continue
            parsed = _order(body, cid)
            _check_forward(cid, o, parsed)
            o.update(parsed)

        by_broker_id = {o["broker_order_id"]: cid for cid, o in orders.items() if o["broker_order_id"]}
        seen = set(self._book["fills_seen"])
        fills = self._read_fills(orders, by_broker_id, seen)

        updates = []
        for cid, o in orders.items():
            if o["status"] != o["reported"]:
                o["reported"] = o["status"]
                updates.append(self._update(cid, orders))
        # Everything parsed: commit.
        self._book["orders"] = orders
        self._book["fills"] += [f.to_record() for f in fills]
        self._book["fills_seen"] = sorted(seen | {f.fill_id for f in fills})
        self._save()
        return [*fills, *updates]

    def _read_fills(self, orders: dict, by_broker_id: dict, seen: set) -> list[Fill]:
        if not by_broker_id:
            return []
        first = min(datetime.fromisoformat(o["intent"]["created_utc"]) for o in orders.values())
        query = {"direction": "asc", "page_size": self.page_size,
                 "after": (first - timedelta(days=1)).strftime("%Y-%m-%d")}
        fills, token = [], None
        while True:
            page = self.api.call("GET", "/v2/account/activities/FILL",
                                 query={**query, **({"page_token": token} if token else {})})
            if not isinstance(page, list):
                raise AlpacaError("malformed fill activities from Alpaca: not a list")
            for act in page:
                fill = self._fill(act, by_broker_id, seen)
                if fill:
                    fills.append(fill)
            if len(page) < self.page_size:
                return fills
            token = page[-1]["id"]

    def _fill(self, act, by_broker_id: dict, seen: set) -> Fill | None:
        need = ("id", "order_id", "qty", "price", "side", "symbol", "transaction_time")
        if not isinstance(act, dict) or any(act.get(k) in (None, "") for k in need):
            raise AlpacaError("malformed fill activity from Alpaca: missing a field")
        if act["id"] in seen:
            return None
        cid = by_broker_id.get(act["order_id"])
        if cid is None:
            self.notes.append(f"fill {act['id']} is on Alpaca order {act['order_id']}, which this book did not place.")
            return None
        qty = _whole(act["qty"], "fill quantity")
        try:
            return Fill(MARKET, act["id"], cid, act["order_id"], act["symbol"], act["side"], qty,
                        float(act["price"]), 0.0, _ts(act["transaction_time"]))
        except (ContractError, ValueError) as e:
            raise AlpacaError(f"malformed fill activity {act['id']} from Alpaca: {e}") from e

    # --- holdings --------------------------------------------------------------------------------

    def positions(self) -> list[Position]:
        body = self.api.call("GET", "/v2/positions")
        if not isinstance(body, list):
            raise AlpacaError("malformed positions from Alpaca: not a list")
        out = []
        for p in body:
            if not isinstance(p, dict) or not p.get("symbol") or p.get("qty") in (None, ""):
                raise AlpacaError("malformed position from Alpaca: missing symbol or qty")
            qty = _whole(p["qty"], f"{p['symbol']} position")
            if qty:
                out.append(Position(MARKET, p["symbol"], qty, float(p.get("avg_entry_price") or 0.0)))
        return out

    def open_orders(self) -> list[OrderUpdate]:
        return [self._update(c) for c, o in self._book["orders"].items() if not OrderStatus(o["status"]).final]

    def reconcile(self) -> list[str]:
        """Differences between Alpaca's holdings and the book's fills; empty when they agree."""
        broker = {p.symbol: p.qty for p in self.positions()}
        book = {p.symbol: p.qty for p in net_positions(MARKET, self._fills())}
        return [f"{s}: Alpaca holds {broker.get(s, 0)}, the book's fills say {book.get(s, 0)}"
                for s in sorted(broker.keys() | book.keys()) if broker.get(s, 0) != book.get(s, 0)]
