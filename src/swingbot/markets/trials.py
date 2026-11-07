"""Phase 3: the NSE and US paper trials as one monthly record, and one report.

Each trial keeps its own files and rules (NSE: docs/research/momentum-paper-trial.md;
US: docs/research/us-momentum-prereg.md). This reads them into the same shape,
`TrialMonth`, so every month answers the plan's questions the same way:
strategy and version, signal date and information cutoff, universe, picks,
orders intended / filled / rejected, value and return after costs, benchmarks,
worst fall, money invested, and anomalies.

The report has one section per market in its own currency, and a side-by-side
table of returns only: rupees and dollars are never compared.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from .profiles import get_profile

NSE_MAIN, US_MAIN = "Mid-cap EW", "RSP"


@dataclass
class TrialMonth:
    market: str
    strategy: str
    version: str
    month: str
    asof: str
    info_cutoff_utc: str
    currency: str
    universe_size: int | None
    picks: list[str]
    orders: tuple[int, int, int]          # intended, filled, rejected
    value: float
    month_return: float
    since_start: float
    bench: dict[str, float | None]
    drawdown: float
    exposure: float | None
    anomalies: list[str] = field(default_factory=list)


def _cutoff(market: str, asof: str) -> str:
    p = get_profile(market)
    d = date.fromisoformat(asof)
    local = datetime.combine(d, p.calendar().close_on(d), tzinfo=p.timezone)
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(now: float | None, first: float | None) -> float | None:
    return now / first - 1 if now and first else None


def from_nse_ledger(ledger: dict) -> list[TrialMonth]:
    capital, months, rows, peak = ledger["capital"], ledger["months"], [], ledger["capital"]
    for i, m in enumerate(months):
        prev = months[i - 1]["value"] if i else capital
        peak = max(peak, m["value"])
        bench = {n: _rel(v, months[0]["indexes"].get(n)) for n, v in m["indexes"].items()}
        bench[NSE_MAIN] = m["ew"]["level"] - 1
        rows.append(TrialMonth(
            "nse", "nse-m6", "M6-1", m["date"][:7], m["date"], _cutoff("nse", m["date"]), "INR",
            m.get("bucket_size"), list(m["picks"]), (len(m["orders"]), len(m["orders"]), 0), m["value"],
            m["value"] / prev - 1, m["value"] / capital - 1, bench, m["value"] / peak - 1,
            (m["value"] - m["cash"]) / m["value"] if m["value"] else None, list(m["notes"])))
    return rows


def from_us_state(root: Path) -> list[TrialMonth]:
    us = Path(root) / "markets" / "us"
    recs = [json.loads(p.read_text()) for p in sorted((us / "us-m12" / "bookings").glob("*.json"))]
    book = us / "alpaca_book.json"
    filled = {f["client_order_id"] for f in json.loads(book.read_text())["fills"]} if book.exists() else set()
    rows, peak = [], 0.0
    for i, r in enumerate(recs):
        first, prev = recs[0], recs[i - 1] if i else recs[0]
        peak = max(peak, r["equity"])
        cids = [o["client_order_id"] for o in r["orders"]]
        rejected = [o for o in r["orders"] if o.get("status") == "rejected"]
        anomalies = list(r.get("reconcile", [])) + list(r.get("notes", []))
        anomalies += [f"{o['client_order_id']} rejected: {o.get('reason', '')}".rstrip(": ") for o in rejected]
        if i < len(recs) - 1:
            gone = {o["client_order_id"] for o in rejected}
            anomalies += [f"{c} was not filled" for c in cids if c not in filled and c not in gone]
        lmv = r.get("long_market_value")
        rows.append(TrialMonth(
            "us", "us-m12", "1", r["month"], r["asof"], _cutoff("us", r["asof"]), "USD", r.get("universe_size"),
            list(r["picks"]), (len(cids), sum(c in filled for c in cids), len(rejected)), r["equity"],
            r["equity"] / prev["equity"] - 1, r["equity"] / first["equity"] - 1,
            {b: _rel(v, first["bench"].get(b)) for b, v in r["bench"].items()}, r["equity"] / peak - 1,
            float(lmv) / r["equity"] if lmv is not None and r["equity"] else None, anomalies))
    return rows


def us_state_from_git(ref: str = "origin/us-trial-state", fetch: bool = True) -> Path:
    """Copy the US trial's records out of the state branch into a temporary folder."""
    if fetch:
        subprocess.run(["git", "fetch", "-q", "origin", ref.split("/", 1)[-1]], check=False)
    out = Path(tempfile.mkdtemp(prefix="us-state-"))
    names = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "markets/us/"], capture_output=True,
                           text=True, check=False).stdout.split()
    for name in names:
        if name.startswith("markets/us/us-m12/bookings/") or name == "markets/us/alpaca_book.json":
            blob = subprocess.run(["git", "show", f"{ref}:{name}"], capture_output=True, check=True).stdout
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            (out / name).write_bytes(blob)
    return out


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def _section(title: str, rows: list[TrialMonth], symbol: str, rule: str) -> list[str]:
    lines = [f"## {title}", ""]
    if not rows:
        return lines + [f"No {title.split()[0]} month booked yet. Rule: {rule}.", ""]
    names = list(rows[-1].bench)
    lines += [f"{rows[0].strategy} version {rows[-1].version}, from {rows[0].asof}. Rule: {rule}.", "",
              "| month | signal close (UTC) | value | month | since start | " + " | ".join(names)
              + " | worst fall | invested | orders filled/intended (rejected) | anomalies |",
              "|---|---|---|---|---|" + "---|" * len(names) + "---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r.month} | {r.info_cutoff_utc} | {symbol}{r.value:,.0f} | {_pct(r.month_return)} | "
                     f"{_pct(r.since_start)} | " + " | ".join(_pct(r.bench.get(n)) for n in names)
                     + f" | {_pct(r.drawdown)} | {_pct(r.exposure)} | {r.orders[1]}/{r.orders[0]} ({r.orders[2]}) | "
                     f"{len(r.anomalies)} |")
    flagged = [(r.month, a) for r in rows for a in r.anomalies]
    if flagged:
        lines += ["", "Anomalies:"] + [f"- {m}: {a}" for m, a in flagged]
    return lines + [""]


def report(nse: list[TrialMonth], us: list[TrialMonth]) -> str:
    lines = ["# Parallel paper trials (phase 3)", ""]
    lines += _section("NSE (INR)", nse, "Rs ", "docs/research/momentum-paper-trial.md")
    lines += _section("US (USD)", us, "$", "docs/research/us-momentum-prereg.md")
    by_nse, by_us = {r.month: r for r in nse}, {r.month: r for r in us}
    lines += ["## Side by side (returns since each trial's start; no currency is compared)", "",
              f"| month | NSE trial | NSE {NSE_MAIN} | US trial | US {US_MAIN} |", "|---|---|---|---|---|"]
    for m in sorted(by_nse.keys() | by_us.keys()):
        n, u = by_nse.get(m), by_us.get(m)
        lines.append(f"| {m} | {_pct(n.since_start) if n else 'n/a'} | {_pct(n.bench.get(NSE_MAIN)) if n else 'n/a'} "
                     f"| {_pct(u.since_start) if u else 'n/a'} | {_pct(u.bench.get(US_MAIN)) if u else 'n/a'} |")
    return "\n".join(lines) + "\n"
