"""Splits and bonus issues: keep the paper account in step with adjusted prices.

The saved prices are adjusted: after a 1:1 bonus every bar before the ex-date
is halved, so the whole history reads in today's shares. The paper ledger is
append-only and keeps what really happened: 100 shares bought at 1,000. Marked
against the halved bars, that holding looks like a 50% loss (a fake drawdown
that can latch the kill switch), and its stop looks hit.

So each split or bonus is listed in `<state_dir>/corporate_actions.csv`:

    symbol,ex_date,ratio,source,noted_at
    ABC,2026-11-12,2,refresh,2026-11-13 08:01     # 1:1 bonus: 2 shares for every 1 held

`ratio` is shares after per share before (a 1:1 bonus is 2, a 2:1 bonus 3, a
split from face value 10 to 2 is 5). Every ledger line dated before the
ex-date is read as `qty x ratio` shares at `price / ratio` (its stop and target
too), and the paper broker moves its live orders the same way when it settles
the ex-date. `noted_at` is when the line was written: an order placed after it
was already priced on the adjusted bars, so the broker leaves it alone. A line
typed by hand with no `noted_at` counts as noted at the start of the ex-date. Profit, loss and R multiples come out the same; share counts and
prices are in today's terms. The ledger file itself is never rewritten.

`swingbot-refresh` adds a line when Yahoo re-adjusts a stock's history by a
split-sized ratio (dividends move it by a percent or two and are left alone).
A line can also be typed by hand. A walk-forward run trades on the adjusted
bars from the start, so its own state folder has no such file.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from pathlib import Path

FILE = "corporate_actions.csv"
MIN_RATIO = 1.1         # a smaller jump is a dividend adjustment, not a split or bonus
RATIO_TOLERANCE = 0.01  # how far a measured ratio may be from a simple fraction


@dataclass(frozen=True)
class Action:
    symbol: str
    ex_date: date
    ratio: float
    source: str = ""
    noted_at: str = ""    # "YYYY-MM-DD HH:MM" IST; empty means the start of the ex-date

    @property
    def noted(self) -> str:
        return self.noted_at or f"{self.ex_date.isoformat()} 00:00"


def load(state_dir: str | Path | None) -> dict[str, list[Action]]:
    """Symbol -> its actions, oldest first. No file, no actions."""
    if state_dir is None:
        return {}
    path = Path(state_dir) / FILE
    if not path.exists():
        return {}
    out: dict[str, list[Action]] = {}
    with path.open(newline="") as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            try:
                a = Action(r["symbol"].strip().upper(), date.fromisoformat(r["ex_date"].strip()),
                           float(r["ratio"]), (r.get("source") or "").strip(), (r.get("noted_at") or "").strip())
            except (KeyError, ValueError) as e:
                raise ValueError(f"{path}:{n}: bad line ({e})") from e
            if a.ratio <= 0:
                raise ValueError(f"{path}:{n}: ratio must be positive")
            out.setdefault(a.symbol, []).append(a)
    for acts in out.values():
        acts.sort(key=lambda a: a.ex_date)
    return out


def add(state_dir: str | Path, action: Action) -> bool:
    """Append an action unless that symbol and ex-date are already listed. True if written."""
    path = Path(state_dir) / FILE
    if any(a.ex_date == action.ex_date for a in load(state_dir).get(action.symbol, [])):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        if new:
            w.writerow(["symbol", "ex_date", "ratio", "source", "noted_at"])
        w.writerow([action.symbol, action.ex_date.isoformat(), f"{action.ratio:g}", action.source, action.noted_at])
    return True


def split_ratio(measured: float) -> float | None:
    """A measured old/new price ratio as a split or bonus ratio, or None if it looks like a dividend."""
    if measured < MIN_RATIO:
        return None
    nice = Fraction(measured).limit_denominator(5)
    return float(nice) if abs(measured / float(nice) - 1) <= RATIO_TOLERANCE else None


def factor(actions: dict[str, list[Action]], symbol: str, day: date) -> float:
    """Shares now per share held on `day`: the product of the ratios with an ex-date after it."""
    f = 1.0
    for a in actions.get(symbol.upper(), []):
        if a.ex_date > day:
            f *= a.ratio
    return f


def shares(qty: int, f: float) -> int:
    """Whole shares after a ratio; a fraction is paid out in cash by the company, not held."""
    return int(qty * f + 1e-9)


PRICE_KEYS = ("price", "stop", "target")


def adjust_records(records: list[dict], actions: dict[str, list[Action]]) -> list[dict]:
    """Ledger records in today's shares and prices. Records with no action after them come back as they are."""
    if not actions:
        return records
    out = []
    for r in records:
        if r.get("kind") not in ("fill", "stop") or r.get("ticker", "").upper() not in actions:
            out.append(r)
            continue
        f = factor(actions, r["ticker"], date.fromisoformat(str(r["at"])[:10]))
        if f == 1.0:
            out.append(r)
            continue
        r = dict(r, adjusted_by=f)
        if "qty" in r:
            r["qty"] = shares(int(r["qty"]), f)
        for k in PRICE_KEYS:
            if r.get(k) is not None:
                r[k] = float(r[k]) / f
        out.append(r)
    return out
