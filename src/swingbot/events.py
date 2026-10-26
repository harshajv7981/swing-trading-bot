"""Load historical news events from CSV or JSONL.

Required columns: event_id, ticker, published_at, headline.
Optional: body, source. Timestamps without an offset are read as IST.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .models import IST, NewsEvent

REQUIRED = ("event_id", "ticker", "published_at", "headline")


def _parse_ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST)
    return ts


def load_events(path: str | Path) -> list[NewsEvent]:
    path = Path(path)
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        df = pd.DataFrame(rows)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")

    events = [
        NewsEvent(
            event_id=str(r["event_id"]),
            ticker=str(r["ticker"]).strip().upper(),
            published_at=_parse_ts(r["published_at"]).to_pydatetime(),
            headline=str(r["headline"]),
            body=str(r.get("body", "") or ""),
            source=str(r.get("source", "") or ""),
        )
        for r in df.to_dict("records")
    ]
    return sorted(events, key=lambda e: e.published_at)
