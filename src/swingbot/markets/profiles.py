"""Market profiles: one explicit object per market instead of scattered conditionals.

A profile holds what differs between NSE and US equities: timezone, sessions,
holidays, currency, tick rules, allowed order types, benchmark, broker and the
environment variables its credentials come from. Credentials themselves are
never stored here.

Session rules always use the market's own timezone, never the host machine's:
every check takes a timezone-aware timestamp and converts it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

from ..risk.exits import nse_tick
from .calendar import MarketCalendar

CONFIG_DIR = Path("config")


def _us_tick(price: float) -> float:
    """Reg NMS Rule 612: quotes at $1.00 or more in whole cents, below $1.00 in $0.0001."""
    return 0.01 if price >= 1.0 else 0.0001


@dataclass(frozen=True)
class MarketProfile:
    key: str                       # short name used in paths: nse | us
    name: str                      # nse_equities | us_equities
    timezone: ZoneInfo
    currency: str
    session_open: time
    session_close: time
    early_close: time | None
    holidays_file: Path
    benchmark: str
    broker: str
    credential_env: tuple[str, ...]
    order_types: frozenset[str]
    time_in_force: frozenset[str]

    def tick(self, price: float) -> float:
        return nse_tick(price) if self.key == "nse" else _us_tick(price)

    def on_tick(self, price: float) -> bool:
        tick = self.tick(price)
        steps = price / tick
        return math.isclose(steps, round(steps), abs_tol=1e-6)

    def calendar(self) -> MarketCalendar:
        return _calendar(self.holidays_file, self.session_close, self.early_close)

    def local(self, at: datetime) -> datetime:
        if at.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware; the host clock's zone is never assumed")
        return at.astimezone(self.timezone)

    def in_session(self, at: datetime) -> bool:
        """True while the regular session is open at `at` (open inclusive, close exclusive)."""
        t = self.local(at)
        cal = self.calendar()
        if not cal.is_session(t.date()):
            return False
        return self.session_open <= t.time() < cal.close_on(t.date())


@cache
def _calendar(path: Path, close: time, early_close: time | None) -> MarketCalendar:
    return MarketCalendar.from_file(path, close, early_close)


NSE = MarketProfile(
    key="nse", name="nse_equities", timezone=ZoneInfo("Asia/Kolkata"), currency="INR",
    session_open=time(9, 15), session_close=time(15, 30), early_close=None,
    holidays_file=CONFIG_DIR / "nse_holidays.toml", benchmark="NIFTY 50", broker="dhan",
    credential_env=("SWINGBOT_DHAN_CLIENT_ID", "SWINGBOT_DHAN_TOKEN"),
    order_types=frozenset({"market", "limit", "stop", "stop_limit"}),
    time_in_force=frozenset({"day"}),
)

US = MarketProfile(
    key="us", name="us_equities", timezone=ZoneInfo("America/New_York"), currency="USD",
    session_open=time(9, 30), session_close=time(16, 0), early_close=time(13, 0),
    holidays_file=CONFIG_DIR / "us_holidays.toml", benchmark="SPY", broker="alpaca",
    credential_env=("SWINGBOT_ALPACA_KEY_ID", "SWINGBOT_ALPACA_SECRET_KEY"),
    # No bracket/OCO yet: the plan allows them only after their behaviour is
    # checked against the internal OCO/stop contract.
    order_types=frozenset({"market", "limit", "stop", "stop_limit"}),
    time_in_force=frozenset({"day", "gtc"}),
)

PROFILES = {p.key: p for p in (NSE, US)}
_ALIASES = {p.name: p.key for p in (NSE, US)}


def get_profile(key: str) -> MarketProfile:
    k = key.strip().lower()
    k = _ALIASES.get(k, k)
    if k not in PROFILES:
        raise KeyError(f"no market profile {key!r}; known: {', '.join(sorted(PROFILES))}")
    return PROFILES[k]
