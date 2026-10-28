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
import html
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


# --- GDELT bulk GKG files ------------------------------------------------------
#
# GDELT also publishes everything it saw as a zipped file every 15 minutes, the
# Global Knowledge Graph (GKG 2.0), at data.gdeltproject.org. Those files have no
# request limit, unlike the DOC API, and reach back to 2015. They are big (all
# the world's news, not one company), so the fetcher streams each one, keeps the
# rows whose page title names a company, and throws the rest away.

GKG_DATE, GKG_SOURCE, GKG_URL, GKG_EXTRAS = 1, 3, 4, 26  # columns of a tab-separated GKG 2.0 row
_GKG_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.DOTALL)

# Indian outlets, matched on the end of GKG's source domain. GKG has no source
# country, so this stands in for the DOC API's `sourcecountry:IN`.
INDIAN_SOURCES = (
    ".in", "indiatimes.com", "livemint.com", "business-standard.com", "moneycontrol.com",
    "thehindubusinessline.com", "thehindu.com", "financialexpress.com", "ndtvprofit.com", "ndtv.com",
    "hindustantimes.com", "indianexpress.com", "news18.com", "zeebiz.com", "zeenews.india.com",
    "cnbctv18.com", "deccanherald.com", "thequint.com", "firstpost.com", "theprint.in", "scroll.in",
    "outlookindia.com", "outlookbusiness.com", "businesstoday.in", "indiatoday.in", "tribuneindia.com",
    "telegraphindia.com", "newindianexpress.com", "dnaindia.com", "freepressjournal.in",
    "ibef.org", "thestatesman.com", "siasat.com", "devdiscourse.com",
    "lokmattimes.com", "republicworld.com", "india.com", "goodreturns.in", "businessinsider.in",
)


_INDIAN_DOMAINS = frozenset(s for s in INDIAN_SOURCES if not s.startswith("."))
_INDIAN_TLDS = tuple(s for s in INDIAN_SOURCES if s.startswith("."))


def indian_source(domain: str) -> bool:
    d = domain.lower().strip()
    if d.endswith(_INDIAN_TLDS):
        return True
    parts = d.split(".")
    return any(".".join(parts[i:]) in _INDIAN_DOMAINS for i in range(len(parts) - 1))


# Other companies whose names start with a universe company's name. A match on one
# of these names nobody, so "L&T Technology Services" is not read as LT. The
# parent still counts when the title names it on its own as well: "SBI sells a
# stake in SBI Funds Management" is SBI news.
OTHER_COMPANIES = (
    "L&T Technology Services", "Larsen & Toubro Technology Services", "Larsen and Toubro Technology Services",
    "L&T Technology", "L&T Tech",
    "L&T Finance", "Larsen & Toubro Finance", "L&T Mutual Fund",
    # Spun out of Vedanta in the 2026 demerger and listed on their own.
    "Vedanta Iron", "Vedanta Oil", "Vedanta Power",  # also covers "Vedanta Iron & Steel", "Vedanta Oil & Gas"
    "SBI Mutual Fund", "SBI MF", "SBI Funds Management", "SBI Funds", "SBI AMC", "SBI Research", "SBI Report",
    # Listed or branded separately from the universe company they start with.
    "SBI Card", "SBI Cards", "SBI Securities", "SBI Capital Markets", "SBI Capital", "SBI Caps", "SBICAPS", "SBI General",
    "Abhinandan Lodha",
    "Shailesh Lodha",
    "Indigo Paints",
    "PNB Housing Finance", "PNB Housing", "PNB MetLife",
    "Mangal Prabhat Lodha",
)

# Titles about a company that are not news about its stock: job notices and fund products.
NOT_NEWS = re.compile(
    # Job notices, but not news about jobs: "TCS job cuts" and "TCS hiring" stay.
    r"\b(admit card|recruitment 20\d\d|vacanc(?:y|ies)|apply online|job (?:openings?|notification|alert)|"
    r"(?:equity|debt|gilt|hybrid|yield|index|bluechip|small ?cap|mid ?cap|large ?cap|flexi ?cap|elss) fund)\b"
    r"|[-|,\u2013]\s*jobs?\s*$",
    re.IGNORECASE)


class HeadlineMatcher:
    """Finds which companies a headline names, with one regex for the whole universe."""

    def __init__(self, companies: list[Company], others: tuple[str, ...] = OTHER_COMPANIES):
        self.by_term: dict[str, list[Company]] = {t.lower(): [] for t in others}
        for c in companies:
            for t in c.terms:
                self.by_term.setdefault(t.lower(), []).append(c)
        terms = sorted(self.by_term, key=len, reverse=True)  # longest first, so "Tata Steel" beats "Tata"
        self.pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.IGNORECASE)

    def companies(self, title: str) -> list[Company]:
        if NOT_NEWS.search(title):
            return []
        found: dict[str, Company] = {}
        for m in self.pattern.finditer(title):
            for c in self.by_term[m.group(1).lower()]:
                found.setdefault(c.symbol, c)
        return list(found.values())

    def refilter(self, rows: list[dict]) -> tuple[list[dict], list[dict]]:
        """Split saved GDELT rows into (kept, dropped) under today's rules, with no new download.

        A row stays when its headline still names its ticker. Rows from other
        sources (NSE filings) are always kept.
        """
        kept, dropped = [], []
        for r in rows:
            if not str(r.get("source", "")).startswith("gdelt:"):
                kept.append(r)
                continue
            names = {c.symbol for c in self.companies(r.get("headline", ""))}
            (kept if r.get("ticker") in names else dropped).append(r)
        return kept, dropped


# A date in the URL (2013/05/07, 2013-05-07, 20130507). GKG includes pages GDELT
# re-crawled years later; their "seen" time is not when the news broke.
_URL_DATE = re.compile(r"(?<!\d)(20\d{2})[/-]?(0[1-9]|1[0-2])[/-]?(0[1-9]|[12]\d|3[01])(?!\d)")
STALE_DAYS = 7


def stale_url(url: str, seen: pd.Timestamp, days: int = STALE_DAYS) -> bool:
    """True when the URL carries a date more than `days` before GDELT saw the page."""
    for m in _URL_DATE.finditer(url):
        try:
            when = pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)), tz=seen.tz)
        except ValueError:
            continue
        if when < seen.normalize() - pd.Timedelta(days=days):
            return True
    return False


def gkg_title(extras: str) -> str:
    m = _GKG_TITLE.search(extras or "")
    return " ".join(html.unescape(m.group(1)).split()) if m else ""


def parse_gkg_date(value: str) -> pd.Timestamp:
    """`20260801123000` (UTC, the 15-minute batch GDELT saw it in) -> IST timestamp."""
    return pd.Timestamp(pd.to_datetime(value, format="%Y%m%d%H%M%S", utc=True)).tz_convert(IST)


def gkg_rows(lines, matcher: HeadlineMatcher, any_source: bool = False) -> list[dict]:
    """Event rows from the lines of one GKG 2.0 file: articles whose title names a company.

    The event_id matches `gdelt_rows` (symbol and URL), so an article found both
    ways is kept once when the files are merged.
    """
    rows = []
    for line in lines:
        # Rows run to tens of kilobytes; split off only the columns needed, cheapest checks first.
        head = line.split("\t", GKG_URL + 1)
        if len(head) <= GKG_URL + 1 or line.count("\t") < GKG_EXTRAS:
            continue
        url, source = head[GKG_URL], head[GKG_SOURCE]
        if not url.startswith("http") or not (any_source or indian_source(source)):
            continue
        title = gkg_title(line.rsplit("\t", 1)[1])
        if not title:
            continue
        hits = matcher.companies(title)
        if not hits:
            continue
        try:
            seen = parse_gkg_date(head[GKG_DATE])
        except ValueError:
            continue
        if stale_url(url, seen):
            continue
        published = seen.isoformat()
        for c in hits:
            rows.append({
                "event_id": _event_id("gd", f"{c.symbol}|{url}"),
                "ticker": c.symbol,
                "published_at": published,
                "headline": title,
                "body": "",
                "source": "gdelt:" + (source or urlparse(url).netloc),
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
