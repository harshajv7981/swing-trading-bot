"""Phase 2: check the Alpaca adapter against the real paper account.

    swingbot-markets alpaca-smoke --phase quiet   # any time: an order that cannot fill
    swingbot-markets alpaca-smoke --phase fill    # market hours: buy 1 SPY, sell it back

The adapter was built and tested against a fake Alpaca; this checks the fake's
assumptions on the real paper account, as the plan's phase 2 asks:

- quiet: the account is ACTIVE in USD; a 1-share SPY buy at $1 is accepted;
  the same client_order_id from a second, empty book is refused by Alpaca (the
  adapter's idempotency depends on it); the order is found by client id;
  cancelling it ends at canceled; two syncs report no fills.
- fill: buy 1 SPY at market and wait for the fill; exactly one share is
  recorded; Alpaca's SPY position goes up by one; another sync does not repeat
  the fill; sell it back; the position is back where it started. Other
  holdings (the trial's) are never touched. Once passed, it is not run again.

The smoke keeps its own book under the output folder, so the trial's book is
never touched. It stops at the first failed check, after cancelling anything
it left open.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from ..markets import Fill, OrderIntent, OrderStatus, get_profile
from .alpaca import AlpacaAPI, AlpacaBroker, AlpacaError

SYMBOL = "SPY"
POLL_SECONDS = 2


class _Failed(Exception):
    pass


class _Run:
    def __init__(self, api: AlpacaAPI, now: datetime, out: Path, sleep, wait: int):
        self.api, self.now, self.out, self.sleep = api, now, out, sleep
        self.polls = max(1, wait // POLL_SECONDS)
        self.stamp = now.strftime("%Y%m%dT%H%M%S")
        self.broker = AlpacaBroker(api, out / "book" / self.stamp)
        self.checks: list[dict] = []
        self.fills: list[Fill] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            raise _Failed(name)

    def intent(self, tag: str, side: str, order_type: str, limit: float | None = None) -> OrderIntent:
        return OrderIntent("us", f"us-smoke-{self.stamp}-{tag}", SYMBOL, side, 1, order_type, "day", self.now,
                           limit_price=limit, strategy="smoke")

    def until_final(self, cid: str) -> OrderStatus:
        status = self.broker._update(cid).status
        for _ in range(self.polls):
            if status.final:
                break
            self.sleep(POLL_SECONDS)
            self.fills += [e for e in self.broker.sync() if isinstance(e, Fill)]
            status = self.broker._update(cid).status
        return status

    def held(self, expect: int | None = None) -> int:
        qty = 0
        for _ in range(5):
            qty = next((p.qty for p in self.broker.positions() if p.symbol == SYMBOL), 0)
            if expect is None or qty == expect:
                break
            self.sleep(POLL_SECONDS)
        return qty

    def account(self) -> None:
        acct = self.api.call("GET", "/v2/account")
        self.check("account", acct.get("status") == "ACTIVE" and acct.get("currency") == "USD",
                   f"{acct.get('status')} {acct.get('currency')}")

    def cleanup(self) -> None:
        for o in self.broker.open_orders():
            try:
                self.broker.cancel(o.client_order_id)
            except Exception as e:          # noqa: BLE001 - cleanup must not hide the failure it follows
                self.checks.append({"check": "cleanup", "ok": False, "detail": f"{o.client_order_id}: {e}"})


def _quiet(r: _Run) -> None:
    r.account()
    o = r.intent("q1", "buy", "limit", 1.0)
    up = r.broker.submit(o)
    r.check("accepted", up.status is OrderStatus.ACCEPTED, f"{up.status.value} {up.broker_order_id}")
    other = AlpacaBroker(r.api, r.out / "book" / f"{r.stamp}-dup")
    dup = other.submit(o)
    if dup.status is not OrderStatus.REJECTED:
        other.cancel(o.client_order_id)
    r.check("duplicate refused", dup.status is OrderStatus.REJECTED, dup.reason or dup.status.value)
    found = r.broker._lookup(o.client_order_id)
    r.check("found by client id", bool(found) and found.get("id") == up.broker_order_id,
            str(found.get("id") if found else None))
    r.broker.cancel(o.client_order_id)
    status = r.until_final(o.client_order_id)
    r.check("cancelled", status is OrderStatus.CANCELLED, status.value)
    first, second = r.broker.sync(), r.broker.sync()
    r.check("sync reports no fills twice", not any(isinstance(e, Fill) for e in first + second) and second == [],
            f"{len(first)} then {len(second)} events")


def _fill(r: _Run) -> None:
    r.account()
    start = r.held()
    buy = r.intent("f1", "buy", "market")
    r.broker.submit(buy)
    status = r.until_final(buy.client_order_id)
    r.check("buy filled", status is OrderStatus.FILLED, status.value)
    r.fills += [e for e in r.broker.sync() if isinstance(e, Fill)]
    mine = [f for f in r.fills if f.client_order_id == buy.client_order_id]
    r.check("one fill recorded", sum(f.qty for f in mine) == 1 and len({f.fill_id for f in mine}) == len(mine)
            and all(f.price > 0 for f in mine), ", ".join(f"{f.qty}@{f.price}" for f in mine))
    up = r.held(start + 1)
    r.check("position up one", up == start + 1, f"{start} -> {up}")
    again = [e for e in r.broker.sync() if isinstance(e, Fill)]
    r.check("sync does not repeat the fill", not again, f"{len(again)} fills")
    sell = r.intent("f2", "sell", "market")
    r.broker.submit(sell)
    status = r.until_final(sell.client_order_id)
    r.check("sell filled", status is OrderStatus.FILLED, status.value)
    back = r.held(start)
    r.check("position back to start", back == start, f"{start + 1} -> {back}")


def run_smoke(api: AlpacaAPI, phase: str, now: datetime, out: Path, sleep=time.sleep, wait: int = 60) -> dict:
    out = Path(out)
    marker = out / "fill-passed.json"
    if phase == "fill":
        if marker.exists():
            return {"ok": True, "skipped": "already passed", "checks": []}
        if not get_profile("us").in_session(now):
            return {"ok": True, "skipped": "market closed", "checks": []}
    r = _Run(api, now, out, sleep, wait)
    try:
        (_quiet if phase == "quiet" else _fill)(r)
        ok = True
    except (_Failed, AlpacaError, ValueError) as e:
        if not isinstance(e, _Failed):
            r.checks.append({"check": "error", "ok": False, "detail": str(e)})
        ok = False
        r.cleanup()
    res = {"ok": ok, "phase": phase, "at": r.stamp, "checks": r.checks, "notes": r.broker.notes}
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{phase}.md").write_text(report(res))
    if ok and phase == "fill":
        marker.write_text(json.dumps(res, indent=1) + "\n")
    return res


def report(res: dict) -> str:
    lines = [f"# Alpaca paper smoke: {res['phase']} ({res['at']}): {'PASS' if res['ok'] else 'FAIL'}", "",
             "| check | result | detail |", "|---|---|---|"]
    lines += [f"| {c['check']} | {'ok' if c['ok'] else 'FAIL'} | {c['detail']} |" for c in res["checks"]]
    lines += [f"\nNote: {n}" for n in res.get("notes", [])]
    return "\n".join(lines) + "\n"
