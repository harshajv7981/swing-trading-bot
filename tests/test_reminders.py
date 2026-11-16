import tomllib
from datetime import date

from swingbot.reminders import FILE, due

ITEMS = [{"date": date(2027, 1, 4), "text": "fund"}]


def test_due_for_a_week_from_its_date():
    assert due(ITEMS, date(2027, 1, 3)) == []
    assert due(ITEMS, date(2027, 1, 4)) == ["fund"]
    assert due(ITEMS, date(2027, 1, 10)) == ["fund"]
    assert due(ITEMS, date(2027, 1, 11)) == []


def test_config_parses_without_double_quotes_in_text():
    items = tomllib.loads(FILE.read_text())["reminder"]
    assert items and all(isinstance(r["date"], date) and '"' not in r["text"] for r in items)
