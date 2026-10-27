"""The learning memory's storage: an episodic ledger of closed trades and the
lessons the Verifier wrote about them (blueprint Section 2).

Everything lives in one local SQLite file, so it costs nothing and needs no
server. Three tables:

* `trades`: one row per closed trade, backtest or paper. The news, the swarm's
  call and what the price did.
* `lessons`: what the Verifier concluded about a trade. A lesson becomes usable
  at its trade's `closed_at`, never earlier. That timestamp is what stops a
  backtest from learning from trades that hadn't finished yet.
* `retrievals`: every time a lesson was put in front of the swarm, and for which
  event. `audit()` checks this log for lookahead.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import pandas as pd

from ..models import IST

# A backtest trade exits at the close of its last session. NSE closes at 15:30 IST.
MARKET_CLOSE = time(15, 30)

VERDICTS = ("right_for_right_reason", "right_for_wrong_reason", "wrong", "no_call")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id     TEXT PRIMARY KEY,
    run          TEXT NOT NULL,
    mode         TEXT NOT NULL,          -- backtest | paper | live
    event_id     TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    published_at TEXT NOT NULL,          -- when the news broke (ISO, tz-aware)
    headline     TEXT NOT NULL,
    sentiment    TEXT NOT NULL,
    confidence   REAL,
    score        REAL,
    rationale    TEXT,
    entry_date   TEXT,
    exit_date    TEXT,
    closed_at    TEXT NOT NULL,          -- when the outcome was known (ISO, tz-aware)
    ret          REAL,
    trade_ret    REAL,
    bench_ret    REAL,
    max_up       REAL,
    max_down     REAL,
    hit          INTEGER,
    extra        TEXT                    -- JSON, e.g. persona views or broker order ids
);
CREATE TABLE IF NOT EXISTS lessons (
    trade_id   TEXT PRIMARY KEY REFERENCES trades(trade_id),
    verdict    TEXT NOT NULL,
    lesson     TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '[]',
    written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrievals (
    event_id     TEXT NOT NULL,
    as_of        TEXT NOT NULL,
    trade_id     TEXT NOT NULL,
    similarity   REAL NOT NULL,
    retrieved_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS trades_closed ON trades(closed_at);
"""


@dataclass(frozen=True)
class ClosedTrade:
    """One finished trade. The paper-trading loop should build these directly
    when a bracket order closes; backtests get them from `from_results`."""

    run: str
    event_id: str
    ticker: str
    published_at: datetime
    headline: str
    sentiment: str
    closed_at: datetime
    confidence: float | None = None
    score: float | None = None
    rationale: str = ""
    entry_date: str | None = None
    exit_date: str | None = None
    ret: float | None = None
    trade_ret: float | None = None
    bench_ret: float | None = None
    max_up: float | None = None
    max_down: float | None = None
    hit: bool | None = None
    mode: str = "backtest"
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("published_at", "closed_at"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{self.event_id}: {name} must be timezone-aware")
        if self.closed_at < self.published_at:
            raise ValueError(f"{self.event_id}: closed before the news broke")

    @property
    def trade_id(self) -> str:
        return f"{self.run}:{self.event_id}"


@dataclass(frozen=True)
class Lesson:
    trade_id: str
    verdict: str
    lesson: str
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"{self.trade_id}: verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if not self.lesson.strip():
            raise ValueError(f"{self.trade_id}: empty lesson")


def _ts(value) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST)
    return ts.to_pydatetime()


def _num(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    return float(value)


# The long-only trade the backtest simulated for this event (backtest._trade_columns).
TRADE_KEYS = ("traded", "no_trade_reason", "trade_entry", "trade_stop", "trade_target",
              "trade_exit_date", "trade_exit_price", "exit_reason", "trade_r")


def _present(value) -> bool:
    return not (value is None or value == "" or (isinstance(value, float) and pd.isna(value)))


def _bool(value) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def from_results(results: pd.DataFrame, run: str, mode: str = "backtest") -> list[ClosedTrade]:
    """Closed trades from a `runs/<run>/results.csv` frame. Only scored rows count.

    Neutral calls are kept: "the swarm sat this one out and the stock ran 6%"
    is a lesson too.
    """
    trades = []
    for r in results.to_dict("records"):
        if r.get("status") != "ok":
            continue
        exit_day = pd.Timestamp(r["exit_date"]).date()
        trades.append(ClosedTrade(
            run=run,
            mode=mode,
            event_id=str(r["event_id"]),
            ticker=str(r["ticker"]),
            published_at=_ts(r["published_at"]),
            headline=str(r["headline"]),
            sentiment=str(r["sentiment"]),
            confidence=_num(r.get("confidence")),
            score=_num(r.get("score")),
            rationale=str(r.get("rationale") or ""),
            entry_date=str(r.get("entry_date")),
            exit_date=str(r["exit_date"]),
            closed_at=datetime.combine(exit_day, MARKET_CLOSE, tzinfo=IST),
            ret=_num(r.get("ret")),
            trade_ret=_num(r.get("trade_ret")),
            bench_ret=_num(r.get("bench_ret")),
            max_up=_num(r.get("max_up")),
            max_down=_num(r.get("max_down")),
            hit=_bool(r.get("hit")),
            extra={"trade": {k: r[k] for k in TRADE_KEYS if k in r and _present(r[k])}},
        ))
    return trades


class MemoryStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- episodic ledger -------------------------------------------------

    def record_trade(self, t: ClosedTrade) -> None:
        """Insert or replace a closed trade. Re-ingesting a run is safe; an
        existing lesson for the trade is kept."""
        self.db.execute(
            """INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trade_id) DO UPDATE SET
                 sentiment=excluded.sentiment, confidence=excluded.confidence, score=excluded.score,
                 rationale=excluded.rationale, entry_date=excluded.entry_date, exit_date=excluded.exit_date,
                 closed_at=excluded.closed_at, ret=excluded.ret, trade_ret=excluded.trade_ret,
                 bench_ret=excluded.bench_ret, max_up=excluded.max_up, max_down=excluded.max_down,
                 hit=excluded.hit, extra=excluded.extra""",
            (t.trade_id, t.run, t.mode, t.event_id, t.ticker, t.published_at.isoformat(), t.headline,
             t.sentiment, t.confidence, t.score, t.rationale, t.entry_date, t.exit_date,
             t.closed_at.isoformat(), t.ret, t.trade_ret, t.bench_ret, t.max_up, t.max_down,
             None if t.hit is None else int(t.hit), json.dumps(t.extra)),
        )
        self.db.commit()

    def trades(self, closed_before: datetime | None = None, without_lesson: bool = False) -> list[sqlite3.Row]:
        sql = "SELECT t.* FROM trades t LEFT JOIN lessons l USING(trade_id) WHERE 1=1"
        if without_lesson:
            sql += " AND l.trade_id IS NULL"
        rows = self.db.execute(sql + " ORDER BY t.closed_at, t.trade_id").fetchall()
        if closed_before is not None:
            rows = [r for r in rows if _ts(r["closed_at"]) < closed_before]
        return rows

    # --- procedural memory ----------------------------------------------

    def add_lesson(self, lesson: Lesson) -> None:
        if self.db.execute("SELECT 1 FROM trades WHERE trade_id=?", (lesson.trade_id,)).fetchone() is None:
            raise KeyError(f"no trade {lesson.trade_id!r} in the ledger")
        self.db.execute(
            "INSERT OR REPLACE INTO lessons VALUES (?,?,?,?,?)",
            (lesson.trade_id, lesson.verdict, lesson.lesson.strip(), json.dumps(list(lesson.tags)),
             datetime.now(IST).isoformat()),
        )
        self.db.commit()

    def lessons_known_at(self, as_of: datetime) -> list[dict]:
        """Lessons whose trade had closed strictly before `as_of`.

        This is the only way retrieval reads lessons, so a backtest event can
        never see a trade that was still open when its news broke.
        """
        rows = self.db.execute(
            "SELECT t.*, l.verdict, l.lesson, l.tags FROM lessons l JOIN trades t USING(trade_id)"
        ).fetchall()
        out = []
        for r in rows:
            if _ts(r["closed_at"]) < as_of:
                d = dict(r)
                d["tags"] = json.loads(d["tags"])
                out.append(d)
        return out

    def log_retrieval(self, event_id: str, as_of: datetime, hits: list[tuple[float, dict]]) -> None:
        now = datetime.now(IST).isoformat()
        self.db.executemany(
            "INSERT INTO retrievals VALUES (?,?,?,?,?)",
            [(event_id, as_of.isoformat(), h["trade_id"], round(s, 4), now) for s, h in hits],
        )
        self.db.commit()

    def audit(self) -> list[str]:
        """Every logged retrieval whose lesson came from a trade that hadn't closed yet."""
        rows = self.db.execute(
            "SELECT r.event_id, r.as_of, r.trade_id, t.closed_at FROM retrievals r JOIN trades t USING(trade_id)"
        ).fetchall()
        return [
            f"{r['event_id']} at {r['as_of']} saw {r['trade_id']}, which closed {r['closed_at']}"
            for r in rows if _ts(r["closed_at"]) >= _ts(r["as_of"])
        ]

    def retrieved_events(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT DISTINCT event_id FROM retrievals")}

    def counts(self) -> dict:
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "trades": q("SELECT COUNT(*) FROM trades"),
            "lessons": q("SELECT COUNT(*) FROM lessons"),
            "retrievals": q("SELECT COUNT(*) FROM retrievals"),
        }
