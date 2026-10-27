"""Turn raw news from GDELT and NSE filings into backtest events.

The fetch scripts in `scripts/` do the network calls. Everything here is pure
parsing so it can be tested offline:

* GDELT articles carry `seendate`, the UTC time GDELT first saw the article,
  usually within 15 minutes of publication. Slightly late is the safe direction.
* NSE announcements carry the exchange dissemination time to the second.

Output rows match `events.load_events`: event_id, ticker, published_at (IST),
headline, body, source, plus a `url` column the loader ignores.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from .models import IST

EVENT_COLUMNS = ["event_id", "ticker", "published_at", "headline", "body", "source", "url"]

# Routine NSE filings that carry no news. Matched against the subject (`desc`).
NSE_ROUTINE = (
    "trading window",
    "newspaper publication",
    "copy of newspaper",
    "certificate under sebi (depositories and participants)",
    "regulation 74(5)",
    "loss of share certificate",
    "duplicate share certificate",
    "updates",  # the generic "Updates" bucket is mostly paperwork
    "esop",
    "esos",
    "allotment of securities",
    "investor presentation",
    "analysts/institutional investor meet/con. call updates",
    "general updates",
    "shareholders meeting",
    "record date",
)


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def mentioned_in(self, text: str) -> bool:
        return any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.IGNORECASE) for t in self.terms)


def load_universe(path: str | Path) -> list[Company]:
    """CSV with symbol,name,aliases. Aliases are `|`-separated and optional."""
    with open(path, newline="") as f:
        return [
            Company(
                r["symbol"].strip().upper(),
                r["name"].strip(),
                tuple(a.strip() for a in (r.get("aliases") or "").split("|") if a.strip()),
            )
            for r in csv.DictReader(f)
        ]


def _event_id(prefix: str, key: str) -> str:
    return f"{prefix}-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


# --- GDELT -------------------------------------------------------------------

GDELT_MIN_TERM = 4  # GDELT refuses shorter phrases


def gdelt_query(company: Company) -> str:
    """DOC API query: any of the company's names, Indian English-language outlets.

    Names with `&` (L&T, M&M) are left out of the query, since GDELT drops the
    punctuation; give a spelled-out alias for those. So are names under 4
    characters (RIL, TCS, SBI): GDELT rejects the whole query with "The
    specified phrase is too short". Both still count when matching headlines.
    """
    names = [t for t in company.terms if "&" not in t and len(t) >= GDELT_MIN_TERM] or [company.name]
    terms = " OR ".join(f'"{t}"' for t in names)
    if len(names) > 1:
        terms = f"({terms})"
    return f"{terms} sourcecountry:IN sourcelang:english"


def parse_gdelt_seendate(value: str) -> pd.Timestamp:
    """`20260801T123000Z` (UTC) -> IST timestamp."""
    return pd.Timestamp(pd.to_datetime(value, format="%Y%m%dT%H%M%SZ", utc=True)).tz_convert(IST)


def gdelt_rows(articles: list[dict], company: Company) -> list[dict]:
    """Keep articles whose headline names the company; GDELT also matches body text,
    which pulls in stories where the company is a passing mention."""
    rows = []
    for a in articles:
        title = (a.get("title") or "").strip()
        url = a.get("url") or ""
        if not title or not url or not a.get("seendate") or not company.mentioned_in(title):
            continue
        rows.append({
            "event_id": _event_id("gd", f"{company.symbol}|{url}"),
            "ticker": company.symbol,
            "published_at": parse_gdelt_seendate(a["seendate"]).isoformat(),
            "headline": title,
            "body": "",
            "source": "gdelt:" + (a.get("domain") or urlparse(url).netloc),
            "url": url,
        })
    return rows


# --- NSE corporate announcements ----------------------------------------------

def _parse_nse_time(value: str) -> pd.Timestamp | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return pd.Timestamp(pd.to_datetime(value, format=fmt)).tz_localize(IST)
        except ValueError:
            continue
    return None


def is_routine(subject: str) -> bool:
    s = subject.strip().lower()
    return any(s == r or s.startswith(r) for r in NSE_ROUTINE)


def nse_rows(announcements: list[dict], keep_routine: bool = False, body_chars: int = 600) -> list[dict]:
    """NSE `/api/corporate-announcements` records -> event rows.

    Uses `exchdisstime` (when the exchange broadcast it), falling back to `an_dt`
    then `sort_date`.
    """
    rows = []
    for a in announcements:
        symbol = (a.get("symbol") or "").strip().upper()
        subject = (a.get("desc") or "").strip()
        ts = next(
            (t for t in (_parse_nse_time(a.get(k, "")) for k in ("exchdisstime", "an_dt", "sort_date")) if t is not None),
            None,
        )
        if not symbol or not subject or ts is None:
            continue
        if not keep_routine and is_routine(subject):
            continue
        name = (a.get("sm_name") or symbol).strip()
        body = " ".join((a.get("attchmntText") or "").split())
        rows.append({
            "event_id": _event_id("nse", f"{symbol}|{a.get('seq_id') or ''}|{ts.isoformat()}|{subject}"),
            "ticker": symbol,
            "published_at": ts.isoformat(),
            "headline": f"{name}: {subject}",
            "body": body[:body_chars],
            "source": "nse-filing",
            "url": a.get("attchmntFile") or "",
        })
    return rows


# --- merging -------------------------------------------------------------------

def _norm(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


def dedupe(rows: list[dict]) -> list[dict]:
    """One row per event_id, and per (ticker, headline): the same story syndicated
    across outlets keeps only its earliest sighting."""
    best: dict[tuple[str, str], dict] = {}
    seen_ids: set[str] = set()
    for r in sorted(rows, key=lambda r: pd.Timestamp(r["published_at"])):
        key = (r["ticker"], _norm(r["headline"]))
        if r["event_id"] in seen_ids or key in best:
            continue
        seen_ids.add(r["event_id"])
        best[key] = r
    return list(best.values())


def merge_into(path: str | Path, rows: list[dict]) -> tuple[int, int]:
    """Merge rows into an events CSV, creating it if needed. Returns (added, total)."""
    path = Path(path)
    existing: list[dict] = []
    if path.exists():
        with open(path, newline="") as f:
            existing = list(csv.DictReader(f))
    merged = dedupe(existing + rows)
    merged.sort(key=lambda r: pd.Timestamp(r["published_at"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")  # written whole, then swapped in
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in EVENT_COLUMNS} for r in merged)
    os.replace(tmp, path)
    return len(merged) - len(dedupe(existing)), len(merged)
