"""swingbot-memory: the trade ledger, the Verifier's lessons, and the with/without score.

  ingest   add a run's closed trades to the ledger
  review   write a Verifier brief of closed trades that have no lesson yet
  learn    load the Verifier's lessons file into memory
  compare  score a run without lessons against a run with them
  audit    check that no brief was shown a lesson from a trade still open
  stats    count trades, lessons and retrievals
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .compare import compare, format_compare
from .store import MemoryStore, _ts, from_results
from .verifier import load_lessons, load_views, review_brief

DEFAULT_DB = "runs/memory.db"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-memory", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite memory file (default {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest", help="add a run's closed trades to the ledger")
    s.add_argument("--results", required=True, help="runs/<run>/results.csv")
    s.add_argument("--run", help="name for this run in the ledger (default: the results folder name)")
    s.add_argument("--views", help="the views JSONL the run scored, so the Verifier sees persona reasoning")
    s.add_argument("--mode", default="backtest", choices=["backtest", "paper", "live"])

    s = sub.add_parser("review", help="write a Verifier brief of trades with no lesson yet")
    s.add_argument("--closed-before", help="only trades closed before this date or time (walk-forward)")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--out", default="runs/verify.md")

    s = sub.add_parser("learn", help="load a lessons JSONL written by the Verifier")
    s.add_argument("--lessons", required=True)

    s = sub.add_parser("compare", help="score a run without memory against one with it")
    s.add_argument("--without", required=True, help="results.csv from briefs without lessons")
    s.add_argument("--with", dest="with_", required=True, help="results.csv from briefs with --with-lessons")
    s.add_argument("--only-with-lessons", action="store_true",
                   help="restrict to events that were shown at least one lesson (reads --db)")

    sub.add_parser("audit", help="check retrievals for lookahead")
    sub.add_parser("stats", help="count what's in memory")
    args = p.parse_args(argv)

    if args.cmd == "compare":
        only = None
        if args.only_with_lessons:
            store = MemoryStore(args.db)
            only = store.retrieved_events()
            store.close()
        c = compare(pd.read_csv(args.without), pd.read_csv(args.with_), only)
        print(format_compare(c))
        return 0

    store = MemoryStore(args.db)
    try:
        if args.cmd == "ingest":
            run = args.run or Path(args.results).parent.name
            trades = from_results(pd.read_csv(args.results), run, args.mode)
            views = load_views(args.views) if args.views else {}
            for t in trades:
                if t.event_id in views:
                    t.extra.update(views[t.event_id])
                store.record_trade(t)
            print(f"Recorded {len(trades)} closed trades from run {run!r} in {args.db}")
        elif args.cmd == "review":
            before = _ts(args.closed_before) if args.closed_before else None
            todo = store.trades(closed_before=before, without_lesson=True)[: args.limit]
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(review_brief(todo))
            left = len(store.trades(closed_before=before, without_lesson=True)) - len(todo)
            print(f"{len(todo)} trades written to {out} ({left} more still need a lesson)")
        elif args.cmd == "learn":
            lessons = load_lessons(args.lessons)
            for lesson in lessons:
                store.add_lesson(lesson)
            print(f"Stored {len(lessons)} lessons in {args.db}")
        elif args.cmd == "audit":
            problems = store.audit()
            for line in problems:
                print("LOOKAHEAD:", line)
            print(f"{store.counts()['retrievals']} retrievals checked, {len(problems)} saw the future")
            return 1 if problems else 0
        elif args.cmd == "stats":
            print(json.dumps(store.counts(), indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
