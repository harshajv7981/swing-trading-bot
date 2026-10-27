"""NSE trading sessions: weekdays minus the holidays in config/nse_holidays.toml."""

from __future__ import annotations

import tomllib
from datetime import date, timedelta
from pathlib import Path

DEFAULT_HOLIDAYS = Path("config/nse_holidays.toml")


class NSECalendar:
    def __init__(self, holidays: dict[int, set[date]] | None = None):
        self.holidays = holidays or {}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "NSECalendar":
        path = Path(path) if path else DEFAULT_HOLIDAYS
        if not path.exists():
            if path != DEFAULT_HOLIDAYS:
                raise FileNotFoundError(f"no holiday file at {path}")
            return cls()
        raw = tomllib.loads(path.read_text()).get("holidays", {})
        return cls({int(y): {date.fromisoformat(d) for d in days} for y, days in raw.items()})

    def knows(self, year: int) -> bool:
        return year in self.holidays

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays.get(day.year, set())

    def sessions_between(self, start: date, end: date) -> int:
        """Sessions after `start`, up to and including `end` (0 when end <= start)."""
        n, day = 0, start
        while day < end:
            day += timedelta(days=1)
            n += self.is_session(day)
        return n

    def next_session(self, day: date) -> date:
        """`day` itself if it is a session, else the first session after it."""
        while not self.is_session(day):
            day += timedelta(days=1)
        return day

    def add_sessions(self, start: date, n: int) -> date:
        """The `n`th session after `start` (`start` itself when n is 0)."""
        day = start
        while n > 0:
            day += timedelta(days=1)
            n -= self.is_session(day)
        return day

    def unknown_years(self, start: date, end: date) -> list[int]:
        return [y for y in range(start.year, end.year + 1) if not self.knows(y)]
