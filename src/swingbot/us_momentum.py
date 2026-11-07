"""US large-cap momentum on Alpaca paper (docs/research/us-momentum-prereg.md).

    swingbot-us-momentum replay --first 2025-10 --last 2026-09   # the code check before the start
    swingbot-us-momentum book [--dry-run]                         # books a month-end, if it is one
    swingbot-us-momentum report                                   # the bookings so far vs RSP, SPY, SPMO

The rule: on the last NYSE session of each month, rank the S&P 500 by the return
from 12 month-ends back to 1 month-end back (split- and dividend-adjusted
closes, 13 month-ends of history needed). Hold the top 20 in equal weight. A
holding still in the top 20 is traded only when it is more than 25% of a slot
away from its slot. Sells go before buys, as market orders for the next open.

`book` runs from the month-end close (+15 minutes) until 09:25 ET the next
session, so a scheduled run that is late still books once; each month is
booked once (its record file), and client order ids make a resend harmless.
Records live under data/state/markets/us/us-m12/ (the state the GitHub Actions
workflow keeps on its own branch).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time as time_module
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, time, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from .broker.alpaca import (
    KEY_ENV,
    SECRET_ENV,
    AlpacaAPI,
    AlpacaBroker,
    AlpacaConfig,
    AlpacaError,
    urllib_transport,
)
from .markets import OrderIntent, get_profile
from .markets.control import utc_stamp

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
BENCH = ("RSP", "SPY", "SPMO")
N, BAND, COST, CASH_BUFFER = 20, 0.25, 0.0005, 0.995
LOOKBACK, SKIP = 12, 1
STRATEGY = "us-m12"
US = get_profile("us")


# --- data ----------------------------------------------------------------------------


class _Constituents(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.in_row, self.cell, self.first, self.text, self.symbols = 0, False, 0, False, "", []

    def handle_starttag(self, tag, attrs):
        if tag == "table" and (self.depth or dict(attrs).get("id") == "constituents"):
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.in_row, self.cell = True, 0
        elif self.depth == 1 and self.in_row and tag == "td":
            self.cell += 1
            self.first, self.text = self.cell == 1, ""

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
        elif self.depth == 1 and tag == "td" and self.first:
            self.first = False
            if self.text.strip():
                self.symbols.append(self.text.strip())

    def handle_data(self, data):
        if self.first:
            self.text += data


def parse_sp500(html: str) -> list[str]:
    p = _Constituents()
    p.feed(html)
    if not p.symbols:
        raise ValueError("no S&P 500 constituents table found in the page")
    return sorted(set(p.symbols))


def fetch_sp500() -> list[str]:
    req = urllib.request.Request(SP500_URL, headers={"User-Agent": "swingbot/0.1 (paper-trading research)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return parse_sp500(resp.read().decode("utf-8", "replace"))


class AlpacaData:
    """Daily adjusted closes from Alpaca's market data API (read-only)."""

    def __init__(self, key_id: str, secret: str, transport=urllib_transport, feed: str = "sip",
                 sleep=time_module.sleep):
        self.transport, self.feed, self.sleep = transport, feed, sleep
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret, "Accept": "application/json"}
        self.notes: list[str] = []

    @classmethod
    def from_env(cls) -> AlpacaData:
        creds = [os.environ.get(k, "").strip() for k in (KEY_ENV, SECRET_ENV)]
        if not all(creds):
            raise AlpacaError(f"{KEY_ENV} and {SECRET_ENV} must be set for Alpaca market data.")
        return cls(*creds, feed=os.environ.get("SWINGBOT_ALPACA_FEED", "sip"))

    def _get(self, query: dict) -> dict:
        for attempt in range(6):
            status, raw = self.transport("GET", DATA_URL + "?" + urllib.parse.urlencode(query),
                                         dict(self.headers), None)
            if status != 429:
                break
            self.sleep(5 * (attempt + 1))           # 200 calls a minute on the free plan
        if status == 403 and query.get("feed") == "sip":
            self.notes.append("the SIP feed was refused (subscription); fell back to IEX closes")
            self.feed = "iex"
            return self._get({**query, "feed": "iex"})
        if status >= 400:
            raise AlpacaError(f"Alpaca market data answered {status}: {raw[:200]!r}")
        return json.loads(raw)

    def daily_closes(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        cols: dict[str, dict[pd.Timestamp, float]] = {}
        for i in range(0, len(symbols), 100):
            query = {"symbols": ",".join(symbols[i:i + 100]), "timeframe": "1Day", "start": start.isoformat(),
                     "end": end.isoformat(), "adjustment": "all", "feed": self.feed, "limit": 10000, "sort": "asc"}
            token = None
            while True:
                body = self._get({**query, **({"page_token": token} if token else {})})
                for sym, bars in (body.get("bars") or {}).items():
                    col = cols.setdefault(sym, {})
                    for b in bars:
                        day = pd.Timestamp(b["t"]).tz_convert(US.timezone).tz_localize(None).normalize()
                        col[day] = float(b["c"])
                token = body.get("next_page_token")
                if not token:
                    break
        df = pd.DataFrame(cols).sort_index()
        return df[[s for s in symbols if s in df.columns]]


def month_ends(daily: pd.DataFrame) -> pd.DataFrame:
    """The last row of each calendar month present in `daily`."""
    return daily.groupby(daily.index.to_period("M")).tail(1)


# --- the rule ---------------------------------------------------------------------------


def rank(m: pd.DataFrame, asof: pd.Timestamp, members: list[str], n: int = N, lookback: int = LOOKBACK,
         skip: int = SKIP) -> tuple[list[str], dict]:
    i = m.index.get_loc(asof)
    if i < lookback:
        return [], {}
    now, recent, past = m.iloc[i], m.iloc[i - skip], m.iloc[i - lookback]
    signal = {}
    for s in members:
        if s in m.columns and pd.notna(now[s]) and pd.notna(recent[s]) and pd.notna(past[s]) and past[s] > 0:
            signal[s] = float(recent[s] / past[s] - 1)
    picks = sorted(signal, key=lambda s: (-signal[s], s))[:n]
    return picks, {s: signal[s] for s in picks}


def _targets(drifted: dict, picks: list[str], n: int, band: float) -> dict:
    slot = 1 / n
    t = {s: (drifted[s] if s in drifted and abs(drifted[s] - slot) <= band * slot else slot) for s in picks}
    total = sum(t.values())
    return {s: w / total for s, w in t.items()} if total else {}


def replay(m: pd.DataFrame, members, first, last, n: int = N, cost: float = COST, band: float = BAND,
           lookback: int = LOOKBACK, skip: int = SKIP, gate=None) -> list[dict]:
    """Book each month-end from `first` to `last` on month-end closes, held to the next month-end.

    `members` is a list, or a function of the month-end date (point-in-time
    membership). `gate(date)` False means hold cash that month.
    """
    rows, drifted = [], {}
    start, stop = m.index.get_loc(pd.Timestamp(first)), m.index.get_loc(pd.Timestamp(last))
    for i in range(start, min(stop, len(m) - 2) + 1):
        asof = m.index[i]
        names = members(asof) if callable(members) else members
        picks = rank(m, asof, names, n, lookback, skip)[0] if gate is None or gate(asof) else []
        target = _targets(drifted, picks, n, band)
        turnover = sum(abs(target.get(s, 0) - drifted.get(s, 0)) for s in set(target) | set(drifted))
        growth, flat = {}, []
        for s in target:
            a, b = m[s].iloc[i], m[s].iloc[i + 1]
            if pd.isna(b):
                flat.append(s)
            growth[s] = 1.0 if pd.isna(b) else float(b / a)
        gross = sum(w * growth[s] for s, w in target.items()) - 1 if target else 0.0
        c = cost * turnover
        bench = {b: float(m[b].iloc[i + 1] / m[b].iloc[i] - 1) for b in BENCH if b in m.columns}
        rows.append({"booked": str(asof.date()), "held_to": str(m.index[i + 1].date()), "picks": picks,
                     "turnover": turnover, "cost": c, "ret": gross - c, "bench": bench, "flat": flat})
        end_value = sum(w * growth[s] for s, w in target.items())
        drifted = {s: w * growth[s] / end_value for s, w in target.items()} if end_value else {}
    return rows


def plan_orders(picks: list[str], held: dict[str, int], prices: dict[str, float], equity: float, n: int,
                band: float, tag: str, at: datetime) -> list[OrderIntent]:
    slot = equity * CASH_BUFFER / n

    def target(s: str) -> int:
        if not prices.get(s):
            raise ValueError(f"no price for {s}; nothing is planned")
        return int(slot // prices[s])

    def intent(s: str, side: str, qty: int) -> OrderIntent:
        return OrderIntent("us", f"{STRATEGY}-{tag}-{s}-{side}", s, side, qty, "market", "day", at,
                           strategy=STRATEGY, strategy_version="1")

    sells, buys = [], []
    for s in sorted(held):
        q = held[s]
        if q <= 0:
            continue
        if s not in picks:
            sells.append(intent(s, "sell", q))
        elif q * prices[s] > slot * (1 + band) and q > target(s):
            sells.append(intent(s, "sell", q - target(s)))
    for s in sorted(picks):
        q, want = held.get(s, 0), target(s)
        if (q == 0 or q * prices[s] < slot * (1 - band)) and want > q:
            buys.append(intent(s, "buy", want - q))
    return sells + buys


# --- booking -------------------------------------------------------------------------------


def _prev_session(d: date) -> date:
    cal = US.calendar()
    d -= timedelta(days=1)
    while not cal.is_session(d):
        d -= timedelta(days=1)
    return d


def booking_month(now: datetime) -> str | None:
    """The month being booked at `now`, or None outside the booking window."""
    local = US.local(now)
    d, cal = local.date(), US.calendar()
    if not cal.is_session(d):
        return None
    after_close = datetime.combine(d, cal.close_on(d)) + timedelta(minutes=15)
    if local.replace(tzinfo=None) >= after_close and cal.next_session(d + timedelta(days=1)).month != d.month:
        return f"{d:%Y-%m}"
    if local.time() < time(9, 25):
        prev = _prev_session(d)
        if prev.month != d.month:
            return f"{prev:%Y-%m}"
    return None


def _shadow(skills, m: pd.DataFrame, members: list[str], month: str, out_dir: Path, record: bool) -> dict:
    """Step 8 shadow runs: settle last month's shadow picks, and pick this month's. No orders.

    Both the shadow picks and the trial's own picks are scored the same way (equal
    weight, close to close, before costs), so they compare like with like.
    """
    from .us_research import trend_gate

    asof, in_shadow = m.index[-1], {k: v for k, v in skills.all("us").items() if v["status"] == "shadow"}
    earlier = sorted(p for p in Path(out_dir).glob("*.json") if p.stem < month)
    prev = json.loads(earlier[-1].read_text()) if earlier else None
    if record and prev and prev.get("shadow") and pd.Timestamp(prev["asof"]) in m.index:
        t0 = pd.Timestamp(prev["asof"])

        def ew(names: list[str]) -> float:
            r = [m.at[asof, s] / m.at[t0, s] - 1 for s in names
                 if s in m.columns and pd.notna(m.at[t0, s]) and pd.notna(m.at[asof, s])]
            return float(sum(r) / len(r)) if r else 0.0

        for key, names in prev["shadow"].items():
            if key in in_shadow:
                skills.record_shadow("us", key, prev["month"], ew(names), ew(prev["picks"]))
    out = {}
    for key, v in in_shadow.items():
        p = v["params"]
        gated = p["trend_gate"] and "SPY" in m.columns and not trend_gate(m["SPY"])(asof)
        out[key] = [] if gated else rank(m, asof, members, p["n"], p["lookback"], p["skip"])[0]
    return out


def book(broker: AlpacaBroker, data, members: list[str], now: datetime, out_dir: Path, n: int = N,
         band: float = BAND, equity: float | None = None, dry_run: bool = False, skills=None) -> dict:
    month = booking_month(now)
    if month is None:
        return {"skipped": "not a booking time"}
    path = Path(out_dir) / f"{month}.json"
    if path.exists():
        return {"skipped": "already booked", "month": month}
    broker.sync()
    held = {p.symbol: p.qty for p in broker.positions()}
    symbols = sorted(set(members) | set(held) | set(BENCH))
    end = US.local(now).date()
    daily = data.daily_closes(symbols, end - timedelta(days=440), end)
    m = month_ends(daily)
    if m.empty or f"{m.index[-1]:%Y-%m}" != month:
        raise RuntimeError(f"the {month} month-end close is not in the data yet; nothing was sent")
    picks, signal = rank(m, m.index[-1], members, n)
    if not picks:
        raise RuntimeError("no stock has 13 month-ends of history; nothing was sent")
    long_value = None
    if equity is None:
        acct = broker.api.call("GET", "/v2/account")
        equity = float(acct["equity"])
        long_value = float(acct["long_market_value"]) if acct.get("long_market_value") is not None else None
    prices = {s: float(v) for s, v in m.iloc[-1].items() if pd.notna(v)}
    intents = plan_orders(picks, held, prices, equity, n, band, month, now)
    rec = {"month": month, "asof": str(m.index[-1].date()), "booked_utc": utc_stamp(now), "picks": picks,
           "signal": signal, "equity": equity, "held_before": held, "universe_size": len(members),
           "long_market_value": long_value,
           "bench": {b: prices.get(b) for b in BENCH}, "reconcile": broker.reconcile(),
           "notes": list(getattr(data, "notes", [])), "dry_run": dry_run, "orders": [],
           "shadow": _shadow(skills, m, members, month, out_dir, not dry_run) if skills is not None else {}}
    for o in intents:
        r = o.to_record()
        if not dry_run:
            up = broker.submit(o)
            r.update(status=up.status.value, broker_order_id=up.broker_order_id, reason=up.reason)
        rec["orders"].append(r)
    rec["notes"] += broker.notes
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return rec


# --- reports ---------------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def replay_report(rows: list[dict], notes: list[str]) -> str:
    lines = ["# US momentum replay (code check, not evidence)", "",
             "| booked | held to | picks | turnover | cost | return | RSP | SPY | SPMO |",
             "|---|---|---|---|---|---|---|---|---|"]
    cum = {"ret": 1.0, **{b: 1.0 for b in BENCH}}
    for r in rows:
        cum["ret"] *= 1 + r["ret"]
        for b in BENCH:
            cum[b] *= 1 + r["bench"].get(b, 0.0)
        lines.append(f"| {r['booked']} | {r['held_to']} | {len(r['picks'])} | {r['turnover']:.2f} | "
                     f"{_pct(r['cost'])} | {_pct(r['ret'])} | {_pct(r['bench'].get('RSP'))} | "
                     f"{_pct(r['bench'].get('SPY'))} | {_pct(r['bench'].get('SPMO'))} |")
    lines += ["", f"**Total over {len(rows)} months:** trial {_pct(cum['ret'] - 1)}, "
              + ", ".join(f"{b} {_pct(cum[b] - 1)}" for b in BENCH), ""]
    flat = sorted({s for r in rows for s in r["flat"]})
    if flat:
        lines.append(f"Held flat for a month with no next close (left the data): {', '.join(flat)}")
    lines += [f"Note: {n}" for n in notes]
    lines.append("Members are today's S&P 500 list, so stocks that left the index are missing: survivorship "
                 "bias. These months were seen before the rule was chosen; this checks the code, not the edge.")
    return "\n".join(lines) + "\n"


def bookings_report(out_dir: Path) -> str:
    recs = [json.loads(p.read_text()) for p in sorted(Path(out_dir).glob("*.json"))]
    if not recs:
        return "No US momentum bookings yet.\n"
    first = recs[0]
    lines = ["| month | equity | since start | RSP | SPY | SPMO | orders | reconcile |",
             "|---|---|---|---|---|---|---|---|"]
    for r in recs:
        since = r["equity"] / first["equity"] - 1
        bench = [(r["bench"][b] / first["bench"][b] - 1) if first["bench"].get(b) and r["bench"].get(b) else None
                 for b in BENCH]
        lines.append(f"| {r['month']} | {r['equity']:,.0f} | {_pct(since)} | " + " | ".join(_pct(b) for b in bench)
                     + f" | {len(r['orders'])} | {'ok' if not r['reconcile'] else '; '.join(r['reconcile'])} |")
    return "\n".join(lines) + "\n"


# --- CLI ----------------------------------------------------------------------------------------


def _members(path: Path | None, save_dir: Path, today: date) -> list[str]:
    if path:
        return [s.strip() for s in path.read_text().split() if s.strip() and s.strip() != "symbol"]
    members = fetch_sp500()
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / f"sp500-{today}.csv").write_text("symbol\n" + "\n".join(members) + "\n")
    return members


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-us-momentum", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("replay")
    p.add_argument("--first", required=True, help="first booking month, YYYY-MM")
    p.add_argument("--last", required=True, help="last booking month, YYYY-MM")
    p.add_argument("--members", type=Path)
    p.add_argument("--out", type=Path, default=Path("data/state/markets/us/us-m12/replay"))
    p = sub.add_parser("book")
    p.add_argument("--members", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--broker-config", type=Path)
    p = sub.add_parser("report")
    p.add_argument("--broker-config", type=Path)
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except (AlpacaError, RuntimeError, ValueError, OSError) as e:
        print(f"swingbot-us-momentum: {e}", file=sys.stderr)
        return 1


def _run(args) -> int:
    now = datetime.now(UTC)
    if args.cmd == "replay":
        first, last = pd.Period(args.first, "M"), pd.Period(args.last, "M")
        members = _members(args.members, args.out, now.date())
        data = AlpacaData.from_env()
        start = (first - LOOKBACK - 1).start_time.date()
        end = min((last + 1).end_time.date(), now.date() - timedelta(days=1))
        m = month_ends(data.daily_closes(sorted(set(members) | set(BENCH)), start, end))
        periods = m.index.to_period("M")
        rows = replay(m, members, m.index[periods == first][0], m.index[periods == last][0])
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "replay.json").write_text(json.dumps(rows, indent=1) + "\n")
        text = replay_report(rows, data.notes)
        (args.out / "replay.md").write_text(text)
        print(text)
        return 0
    cfg = AlpacaConfig.load(args.broker_config, required=False)
    out = cfg.state_dir / STRATEGY / "bookings"
    if args.cmd == "report":
        print(bookings_report(out))
        return 0
    if booking_month(now) is None:
        print("not a booking time (the last NYSE session after the close, until 09:25 ET the next session)")
        return 0
    broker = AlpacaBroker(AlpacaAPI.from_env(cfg))
    members = _members(args.members, cfg.state_dir / STRATEGY / "universe", now.date())
    from .markets.skills import Skills
    rec = book(broker, AlpacaData.from_env(), members, now, out, dry_run=args.dry_run, skills=Skills(cfg.state_root))
    print(json.dumps({k: rec.get(k) for k in ("month", "skipped", "picks", "equity", "reconcile", "notes")}, indent=1))
    for o in rec.get("orders", []):
        print(f"{o['side']:4} {o['qty']:>6} {o['symbol']:6} {o.get('status', 'dry-run')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
