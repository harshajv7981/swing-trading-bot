"""Enough of Alpaca's Trading API v2 to drive the adapter in tests.

Field names follow Alpaca's docs (orders, positions, account activities of
type FILL); the real paper account has not been checked against this yet.
"""

import json
import urllib.parse


class Unreachable(OSError):
    pass


class FakeAlpaca:
    def __init__(self):
        self.calls = []
        self.orders: dict[str, dict] = {}          # alpaca id -> order
        self.activities: list[dict] = []
        self.positions: dict[str, dict] = {}       # symbol -> position
        self.n = 0
        self.lose_next_post = None                 # "before" | "after": the POST response never arrives
        self.reject_next_post = False
        self.override_next: dict | None = None     # replace the next GET order body (malformed / stale tests)
        self.fill_qty_override = None
        self.auto_fill = None                      # a price: market orders fill as soon as they arrive
        self.allow_duplicates = False              # a broken Alpaca that accepts a reused client_order_id

    # --- transport ---------------------------------------------------------------

    def __call__(self, method, url, headers, body):
        assert headers["APCA-API-KEY-ID"] == "kid" and headers["APCA-API-SECRET-KEY"] == "sec"
        parsed = urllib.parse.urlparse(url)
        assert parsed.netloc == "paper-api.alpaca.markets"
        path, query = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        payload = json.loads(body) if body else None
        self.calls.append((method, path, payload or query))
        if method == "POST" and path == "/v2/orders":
            return self._post(payload)
        if method == "GET" and path == "/v2/orders:by_client_order_id":
            for o in self.orders.values():
                if o["client_order_id"] == query["client_order_id"]:
                    return self._ok(o)
            return 404, json.dumps({"code": 40410000, "message": "order not found"}).encode()
        if method == "GET" and path.startswith("/v2/orders/"):
            o = self.orders[path.rsplit("/", 1)[1]]
            if self.override_next is not None:
                o, self.override_next = self.override_next, None
            return self._ok(o)
        if method == "DELETE" and path.startswith("/v2/orders/"):
            o = self.orders[path.rsplit("/", 1)[1]]
            if o["status"] in ("filled", "canceled", "expired", "rejected"):
                return 422, json.dumps({"code": 42210000, "message": "order is not cancelable"}).encode()
            o["status"] = "canceled"
            return 204, b""
        if method == "GET" and path == "/v2/account/activities/FILL":
            return self._activities(query)
        if method == "GET" and path == "/v2/positions":
            return self._ok(list(self.positions.values()))
        if method == "GET" and path == "/v2/account":
            return self._ok({"status": "ACTIVE", "currency": "USD", "cash": "100000", "buying_power": "200000"})
        return 404, b'{"message": "not found"}'

    def _ok(self, obj):
        return 200, json.dumps(obj).encode()

    def _post(self, p):
        if self.lose_next_post == "before":
            self.lose_next_post = None
            raise Unreachable("connection reset")
        if self.reject_next_post:
            self.reject_next_post = False
            return 403, json.dumps({"code": 40310000, "message": "insufficient buying power"}).encode()
        if not self.allow_duplicates and any(o["client_order_id"] == p["client_order_id"]
                                             for o in self.orders.values()):
            return 422, json.dumps({"code": 40010001, "message": "client_order_id must be unique"}).encode()
        self.n += 1
        oid = f"a{self.n:04d}"
        self.orders[oid] = {"id": oid, "client_order_id": p["client_order_id"], "symbol": p["symbol"],
                            "side": p["side"], "qty": p["qty"], "type": p["type"],
                            "time_in_force": p["time_in_force"], "limit_price": p.get("limit_price"),
                            "stop_price": p.get("stop_price"), "status": "accepted", "filled_qty": "0",
                            "updated_at": "2026-11-02T15:00:00.123456789Z"}
        if self.auto_fill and p["type"] == "market":
            self.execute(p["client_order_id"], int(p["qty"]), self.auto_fill)
        if self.lose_next_post == "after":
            self.lose_next_post = None
            raise Unreachable("read timed out")
        return self._ok(self.orders[oid])

    def _activities(self, q):
        acts = sorted(self.activities, key=lambda a: a["id"])
        if q.get("page_token"):
            acts = [a for a in acts if a["id"] > q["page_token"]]
        return self._ok(acts[: int(q.get("page_size", 100))])

    # --- the venue: what the market does to orders ---------------------------------------

    def _by_client(self, client_order_id):
        return next(o for o in self.orders.values() if o["client_order_id"] == client_order_id)

    def execute(self, client_order_id, qty, price, at="2026-11-02T15:05:00Z"):
        o = self._by_client(client_order_id)
        filled = int(o["filled_qty"]) + qty
        o["filled_qty"] = str(filled)
        o["status"] = "filled" if filled == int(o["qty"]) else "partially_filled"
        self.activities.append({
            "id": f"20261102150500000::{len(self.activities):04d}", "activity_type": "FILL",
            "transaction_time": at, "type": "fill" if o["status"] == "filled" else "partial_fill",
            "price": str(price), "qty": str(self.fill_qty_override or qty), "side": o["side"],
            "symbol": o["symbol"], "leaves_qty": str(int(o["qty"]) - filled), "order_id": o["id"],
            "cum_qty": str(filled), "order_status": o["status"]})
        pos = self.positions.setdefault(o["symbol"], {"symbol": o["symbol"], "qty": "0",
                                                     "avg_entry_price": "0", "side": "long"})
        held = int(pos["qty"]) + (qty if o["side"] == "buy" else -qty)
        if o["side"] == "buy":
            pos["avg_entry_price"] = str((float(pos["avg_entry_price"]) * int(pos["qty"]) + price * qty) / held)
        pos["qty"] = str(held)
        if held == 0:
            del self.positions[o["symbol"]]

    def reject(self, client_order_id):
        self._by_client(client_order_id)["status"] = "rejected"

    def expire(self, client_order_id):
        self._by_client(client_order_id)["status"] = "expired"
