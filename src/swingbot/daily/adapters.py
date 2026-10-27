"""Hooks that call steps built in their own modules, falling back to the stubs
when that module isn't installed yet. Point config/daily.toml at these."""

from __future__ import annotations

from . import stubs
from .hooks import FAILED, OK, Step


def refresh(day) -> Step:
    """`swingbot.data.refresh.run` (the swingbot-refresh CLI), cut to the loop's clock."""
    try:
        from ..data.refresh import run
        from ..data.router import Router
    except ImportError:
        return stubs.refresh(day)
    try:
        report = run(Router(day.data), now=day.as_of, calendar=day.calendar)
    except RuntimeError as e:  # another refresh holds the lock
        return Step("data refresh", FAILED, f"{e}; going on with the data already saved")
    text = report.text().strip().splitlines()
    detail = "; ".join(line.strip() for line in text) if text else "nothing to update"
    if report.ok:
        return Step("data refresh", OK, detail)
    failed = report.failed()
    return Step("data refresh", FAILED, f"{detail}; {len(failed)} item(s) failed, older data is used for them")
