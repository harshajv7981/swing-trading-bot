"""Placeholder hooks for the daily loop, until the real steps are built.

Each one does nothing to the account and says plainly what is missing, so a
daily report never looks complete when it isn't. Replace them in
config/daily.toml; the contracts are in `hooks.py`.
"""

from __future__ import annotations

from .hooks import STUB, Step


def refresh(day) -> Step:
    return Step("data refresh", STUB, (
        "no refresh step yet: prices come from saved CSVs (and Yahoo through the data router), "
        f"news from {day.data.events_file} as last fetched. Run the scripts/ fetchers by hand for fresh news."))


def broker_settle(day) -> Step:
    return Step("paper broker settle", STUB, (
        "no paper broker yet: nothing filled or exited by itself. Record what would have filled with "
        "`swingbot-paper fill` before trusting the report."))


def signal_to_order(day, signals, portfolio, mood) -> list:
    bullish = [s for s in signals if s["sentiment"] == "bullish"]
    if bullish:
        names = ", ".join(f"{s['ticker']} ({s['confidence']:.2f})" for s in bullish)
        day.note(f"no signal-to-order step yet: {len(bullish)} bullish signal(s) not turned into orders: {names}. "
                 "The risk-check skill can size one by hand.")
    return []


def broker_submit(day, tickets) -> Step:
    if not tickets:
        return Step("paper broker submit", STUB, "no paper broker yet, and no tickets today")
    return Step("paper broker submit", STUB, (
        f"no paper broker yet: {len(tickets)} ticket(s) written under {day.dir / 'tickets'} but not held "
        "anywhere. Nothing was sent to a real broker."))
