"""A market calendar: the NSE calendar plus early closes, read from one TOML file.

The file has a `[holidays]` table (year -> dates) and, optionally, an
`[early_closes]` table in the same shape. NSE has no regular early closes, so
its file has none and `close_on` always gives the profile's normal close.
"""

from __future__ import annotations

import tomllib
from datetime import date, time
from pathlib import Path

from ..risk.calendar import NSECalendar


class MarketCalendar(NSECalendar):
    def __init__(self, holidays: dict[int, set[date]] | None = None, early_closes: set[date] | None = None,
                 close: time = time(16, 0), early_close: time | None = None):
        super().__init__(holidays)
        self.early_closes = early_closes or set()
        self.close, self.early_close = close, early_close

    @classmethod
    def from_file(cls, path: Path, close: time, early_close: time | None) -> MarketCalendar:
        if not path.exists():
            return cls(close=close, early_close=early_close)
        raw = tomllib.loads(path.read_text())
        holidays = {int(y): {date.fromisoformat(d) for d in days} for y, days in raw.get("holidays", {}).items()}
        early = {date.fromisoformat(d) for days in raw.get("early_closes", {}).values() for d in days}
        return cls(holidays, early, close, early_close)

    def close_on(self, day: date) -> time:
        """The closing bell on `day`, in market-local time."""
        if self.early_close is not None and day in self.early_closes:
            return self.early_close
        return self.close
