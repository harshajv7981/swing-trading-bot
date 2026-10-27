"""Each company's next results date, read from its board meeting notices (rule R9).

Indian companies must tell the exchange before the board meets to approve
results, and NSE broadcasts that as a "Board Meeting Intimation" filing,
usually with a sentence like "Board meeting of the Company will be held on
14-Nov-2026, inter alia, to consider and approve the financial results". Those
filings already come into the events file through the NSE fetcher and
`swingbot-refresh --accept-nse-terms`, so the dates cost nothing extra.

Point in time: a notice counts only once it was published, so a backtest or a
past as-of date never sees a meeting that hadn't been announced yet. When a
company files more than one notice (a date moved, a second meeting), the most
recently published notice for a meeting still ahead wins.

A company with no notice gets no date. That is "unknown", not "no results
soon": R9 then warns, or blocks once `require_results_date` is on.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from ..models import IST, NewsEvent

MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
DATE_PATTERNS = (
    # 14-Nov-2026, 14 Nov 2026, 14th November, 2026
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.,]*{_MON}[\s\-/.,]*(\d{{4}})\b", re.I),
    # November 14, 2026
    re.compile(rf"\b{_MON}[\s.]*(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})\b", re.I),
    # 14/11/2026, 14-11-2026, 14.11.2026 (Indian day-first order)
    re.compile(r"\b(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{4})\b"),
)
# The date that matters follows "held on", "scheduled on" and the like.
ANCHOR = re.compile(r"\b(?:held|scheduled|convened|meet|meeting)\b[^.]{0,40}?\bon\b", re.I)


@dataclass(frozen=True)
class Notice:
    ticker: str
    meeting: date
    published_at: datetime
    event_id: str


def _to_date(groups: tuple[str, ...], kind: int) -> date | None:
    try:
        if kind == 0:
            d, mon, y = groups
            return date(int(y), MONTHS[mon[:3].lower()], int(d))
        if kind == 1:
            mon, d, y = groups
            return date(int(y), MONTHS[mon[:3].lower()], int(d))
        d, m, y = groups
        return date(int(y), int(m), int(d))
    except (KeyError, ValueError):
        return None


def meeting_date(text: str) -> date | None:
    """The board meeting date in a notice: the first date after "held on", else the first date at all."""
    starts = [m.end() for m in ANCHOR.finditer(text)] + [0]
    for start in starts:
        found = []
        for kind, pattern in enumerate(DATE_PATTERNS):
            m = pattern.search(text, start)
            if m and (d := _to_date(m.groups(), kind)):
                found.append((m.start(), d))
        if found:
            return min(found)[1]
    return None


def is_results_notice(event: NewsEvent) -> bool:
    """A board meeting intimation whose business includes the financial results."""
    head, body = event.headline.lower(), event.body.lower()
    return (event.source.startswith("nse") and "board meeting" in head and "outcome" not in head
            and "result" in f"{head} {body}")


def notices(events: list[NewsEvent]) -> list[Notice]:
    out = []
    for e in events:
        if not is_results_notice(e):
            continue
        d = meeting_date(e.body) or meeting_date(e.headline)
        # A meeting dated before its own notice is a misread (or a late filing): skip it.
        if d is not None and d >= pd.Timestamp(e.published_at).tz_convert(IST).date():
            out.append(Notice(e.ticker, d, e.published_at, e.event_id))
    return out


def next_results_dates(events: list[NewsEvent], as_of) -> dict[str, date]:
    """Symbol -> the next results meeting on or after `as_of`, from notices published by then."""
    as_of = pd.Timestamp(as_of)
    as_of = as_of.tz_localize(IST) if as_of.tzinfo is None else as_of.tz_convert(IST)
    today = as_of.date()
    best: dict[str, Notice] = {}
    for n in notices(events):
        if n.published_at > as_of or n.meeting < today:
            continue
        prev = best.get(n.ticker)
        if prev is None or n.published_at > prev.published_at:
            best[n.ticker] = n
    return {t: n.meeting for t, n in sorted(best.items())}


def write_csv(dates: dict[str, date], path: str | Path) -> None:
    """The `symbol,date` file `swingbot-risk orders --results-dates` reads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "date"])
        w.writerows([t, d.isoformat()] for t, d in dates.items())


DEFAULT_MANUAL = Path("data/state/results_dates.csv")


def load_manual(path: str | Path | None) -> dict[str, list[date]]:
    """Dates typed in by hand (`symbol,date`), for companies whose notices weren't collected.

    A symbol may have several lines (this quarter and the next, or a whole
    season for a walk-forward), and each date counts until it has passed.
    A typed date has no publish time, so it counts as known all along. That is
    close to the truth: listed companies must give the exchange at least two
    working days' notice of a results meeting (SEBI LODR rule 29), and R9 only
    looks two sessions ahead.
    """
    path = Path(path) if path else None
    if path is None or not path.exists():
        return {}
    out: dict[str, list[date]] = {}
    with path.open(newline="") as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            try:
                out.setdefault(r["symbol"].strip().upper(), []).append(date.fromisoformat(r["date"].strip()))
            except (KeyError, AttributeError, ValueError) as e:
                raise ValueError(f"{path}:{n}: bad line, want symbol,date as YYYY-MM-DD ({e})") from e
    return {t: sorted(ds) for t, ds in out.items()}


def from_router(router, as_of, manual: str | Path | None = DEFAULT_MANUAL) -> dict[str, date]:
    """Next results dates from the events file the data router reads, merged with hand-typed dates.

    Each company gets the earliest date still ahead from either source, so a
    stale typed date can't hide a newer notice, and a notice can't hide a
    sooner typed date.
    """
    from ..data.router import DataError

    try:
        events = router.context.events()
    except DataError:
        events = []
    dates = next_results_dates(events, as_of)
    ts = pd.Timestamp(as_of)
    today = (ts.tz_convert(IST) if ts.tzinfo else ts).date()
    for t, ds in load_manual(manual).items():
        ahead = [d for d in ds if d >= today]
        if ahead and (t not in dates or ahead[0] < dates[t]):
            dates[t] = ahead[0]
    return dict(sorted(dates.items()))
