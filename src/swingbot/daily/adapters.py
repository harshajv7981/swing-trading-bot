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


def broker_settle(day) -> Step:
    """`swingbot.broker.daily.settle` (the paper broker) when installed, else the stub."""
    try:
        from ..broker.daily import settle
    except ImportError:
        return stubs.broker_settle(day)
    return settle(day)


def broker_submit(day, tickets) -> Step:
    """`swingbot.broker.daily.submit` (the paper broker) when installed, else the stub."""
    try:
        from ..broker.daily import submit
    except ImportError:
        return stubs.broker_submit(day, tickets)
    return submit(day, tickets)


def signal_to_order(day, signals, portfolio, mood) -> list:
    """`swingbot.risk.signals.daily_hook` (signal-to-order) when installed, else the stub."""
    try:
        from ..risk.signals import daily_hook
    except ImportError:
        return stubs.signal_to_order(day, signals, portfolio, mood)
    return daily_hook(day, signals, portfolio, mood)
