"""Milestone reminders from config/reminders.toml, shown by the nightly launchd job.

    python -m swingbot.reminders
"""
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

from .momentum import notify

FILE = Path("config/reminders.toml")
DAYS = 7  # repeat for a week, so a night the Mac was off doesn't lose the reminder


def due(items: list[dict], today: date) -> list[str]:
    return [r["text"] for r in items if r["date"] <= today < r["date"] + timedelta(days=DAYS)]


def main() -> int:
    for text in due(tomllib.loads(FILE.read_text())["reminder"], date.today()):
        notify(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
