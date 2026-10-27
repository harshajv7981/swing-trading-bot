"""NSE hours for the daily loop: is today a session, and where in it are we.

Normal trading runs 09:15 to 15:30 IST on session days (weekdays minus the
holidays in config/nse_holidays.toml). The 09:00 to 09:15 pre-open auction is
counted as before the open: a DAY limit order placed then is in the book at 09:15.
Its order entry closes at 09:08, so news after 09:08 belongs to the next
session's window (`news_window`), the same cut the backtest uses.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from ..models import AUCTION_CUTOFF, IST
from ..risk.calendar import NSECalendar

OPEN = time(9, 15)
CLOSE = time(15, 30)

HOLIDAY = "holiday"      # weekend or NSE holiday: no session today
PRE_OPEN = "pre_open"    # a session day, before 09:15
OPEN_NOW = "open"        # 09:15 to 15:30
CLOSED = "closed"        # a session day, after 15:30


def ist(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def phase(now, calendar: NSECalendar) -> str:
    now = ist(now)
    if not calendar.is_session(now.date()):
        return HOLIDAY
    t = now.time()
    if t < OPEN:
        return PRE_OPEN
    return OPEN_NOW if t < CLOSE else CLOSED


def previous_session(day: date, calendar: NSECalendar) -> date:
    """The last session strictly before `day`."""
    d = day - timedelta(days=1)
    while not calendar.is_session(d):
        d -= timedelta(days=1)
    return d


def next_session(day: date, calendar: NSECalendar) -> date:
    """The first session strictly after `day`."""
    d = day + timedelta(days=1)
    while not calendar.is_session(d):
        d += timedelta(days=1)
    return d


def at(day: date, t: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, t), tz=IST)


def news_window(now, calendar: NSECalendar) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The news today's run trades on: from the previous session's open up to now.

    This matches the backtest's timing. News before 09:08 (when pre-open order
    entry closes) enters at that day's open and news after it at the next
    session's open, so everything published since 09:08 on the last session is
    first tradeable at today's open. Older
    news had a session to be priced in and is left out, even when a day was
    skipped.
    """
    now = ist(now)
    return at(previous_session(now.date(), calendar), AUCTION_CUTOFF), now
