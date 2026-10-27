"""swingbot-backtest: run a swarm over historical news and score it."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .backtest import run_backtest
from .events import DEFAULT_LLM_CUTOFF, apply_cutoff, load_events
from .metrics import format_summary, summarise
from .models import IST
from .prices import CSVPriceSource, YFinancePriceSource
from .risk.config import RiskConfig
from .swarm import build_engine

CUTOFF_HELP = (
    "drop news published before this date (YYYY-MM-DD) so an LLM swarm can't "
    f"remember the outcome; 'none' turns it off. Default {DEFAULT_LLM_CUTOFF} for "
    "the claude and recorded engines and for briefs, off for lexicon"
)


def _load(path: str, cutoff: str | None) -> list:
    events, dropped = apply_cutoff(load_events(path), cutoff)
    if dropped:
        print(f"Dropped {dropped} events published before the {cutoff} training cutoff.")
    return events


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events", required=True, help="CSV or JSONL of news events")
    p.add_argument("--prices", help="directory of <TICKER>.csv daily bars")
    p.add_argument("--yahoo", nargs=2, metavar=("START", "END"),
                   help="fetch NSE bars from Yahoo instead, e.g. --yahoo 2025-01-01 2026-10-01")
    p.add_argument("--engine", default="lexicon", choices=["lexicon", "claude", "recorded", "mirofish"])
    p.add_argument("--views", help="persona views JSONL for --engine recorded")
    p.add_argument("--crowd-only", action="store_true",
                   help="with --engine recorded, ignore the Bull/Bear judge and score the persona crowd")
    p.add_argument("--model", help="Claude model for --engine claude")
    p.add_argument("--rounds", type=int, default=2, help="swarm interaction rounds")
    p.add_argument("--horizon", default="72h", help="signal horizon, e.g. 48h or 72h")
    p.add_argument("--benchmark", help="ticker of a benchmark series, e.g. NIFTY50 (CSV) or ^NSEI (Yahoo)")
    p.add_argument("--cost-bps", type=float, default=25.0, help="round-trip cost per trade in bps")
    p.add_argument("--stop-rule", default="atr:2", help="exit planner stop for long trades: atr:K, pct:X or low:N")
    p.add_argument("--target-rule", default="2R", help="exit planner target for long trades, e.g. 2R")
    p.add_argument("--no-trail", action="store_true", help="keep the initial stop instead of trailing it (R7)")
    p.add_argument("--out", default="runs", help="directory for results")
    p.add_argument("--cutoff", help=CUTOFF_HELP)
    args = p.parse_args(argv)

    if bool(args.prices) == bool(args.yahoo):
        p.error("pass exactly one of --prices or --yahoo")
    prices = CSVPriceSource(args.prices) if args.prices else YFinancePriceSource(*args.yahoo)

    cutoff = args.cutoff or (DEFAULT_LLM_CUTOFF if args.engine in ("claude", "recorded") else None)
    events = _load(args.events, cutoff)
    engine = build_engine(args.engine, rounds=args.rounds, horizon=args.horizon, model=args.model, views=args.views)
    if args.crowd_only:
        if args.engine != "recorded":
            p.error("--crowd-only only applies to --engine recorded")
        engine.crowd_only()
    results = run_backtest(events, engine, prices, benchmark=args.benchmark, cost_bps=args.cost_bps,
                           stop_rule=args.stop_rule, target_rule=args.target_rule, config=RiskConfig.load(),
                           trail=not args.no_trail)
    summary = summarise(results)

    out = Path(args.out) / f"{datetime.now():%Y%m%d-%H%M%S}-{engine.name}"
    out.mkdir(parents=True, exist_ok=True)
    results.to_csv(out / "results.csv", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    print(f"Engine: {engine.name}  rounds: {args.rounds}  horizon: {args.horizon}")
    if any(e.source == "synthetic" for e in events):
        print("NOTE: synthetic sample data. These numbers only show the pipeline works.")
    print(format_summary(summary))
    print(f"\nWrote {out}/results.csv and summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


TIME_RULE = """Every event below broke on {day} (IST) and they are listed in the order they
were published. Judge each event as if it were that event's timestamp: use only
its own block and the events listed above it. Never let a story listed below
it, or anything you know about what happened afterwards, change its views."""


def _ist_day(event):
    return event.published_at.astimezone(IST).date()


def _same_day_batch(events: list, limit: int) -> list:
    """The first `limit` events from the earliest IST day in `events`.

    Batching across days would put later headlines in front of Claude while it
    judges earlier ones, which is look-ahead leakage in a backtest.
    """
    if not events:
        return []
    events = sorted(events, key=lambda e: e.published_at)
    day = _ist_day(events[0])
    return [e for e in events if _ist_day(e) == day][:limit]


def briefs_main(argv: list[str] | None = None) -> int:
    """swingbot-briefs: write the brief Claude Code role-plays from.

    By default the brief holds the personas and the events, never prices.
    --with-technicals and --with-context add data known when each story broke. Events that
    already have views in --views are left out, so a long run can be done in
    batches.

    A brief only ever holds events from one IST calendar day, the earliest day
    still to do, so a later day's headline can't sit next to an earlier one and
    leak into its judgment. Within that day events are listed in publication
    order and the brief tells Claude to judge each one only from itself and the
    stories above it. --limit 1 isolates every event completely.
    """
    from .swarm import DEFAULT_PERSONAS
    from .swarm.reasoners import SYSTEM_PROMPT
    from .data import DataConfig, Router
    from .swarm.recorded import ANALYST_PROMPT, DEBATE_PROMPT, RecordedSwarm

    p = argparse.ArgumentParser(description=briefs_main.__doc__)
    p.add_argument("--events", required=True)
    p.add_argument("--with-technicals", metavar="PRICES_DIR",
                   help="add a technical snapshot per event, built only from bars known at publication")
    p.add_argument("--with-mood", metavar="PRICES_DIR",
                   help="add the market mood (Nifty trend, FMCG, gold, USD/INR, India VIX) as of each story, "
                        "from saved CSVs in this directory")
    p.add_argument("--with-context", action="store_true",
                   help="add the week's earlier news, filings and the latest results filing for each "
                        "event's stock, taken from the same events file")
    p.add_argument("--views", help="existing views JSONL; events already in it are skipped")
    p.add_argument("--limit", type=int, default=10,
                   help="max events in this brief; all come from the same IST day")
    p.add_argument("--out", default="runs/brief.md")
    p.add_argument("--cutoff", default=DEFAULT_LLM_CUTOFF, help=CUTOFF_HELP)
    p.add_argument("--with-lessons", metavar="MEMORY_DB",
                   help="add the Verifier's lessons from similar trades that closed before each story broke")
    p.add_argument("--lessons-k", type=int, default=3, help="max lessons per event")
    p.add_argument("--until", help="only events published before this date (one walk-forward window)")
    args = p.parse_args(argv)
    memory = None
    if args.with_lessons:
        from .memory import MemoryStore
        from .memory.retrieval import MEMORY_PROMPT, brief_section
        memory = MemoryStore(args.with_lessons)
    # Pre-fetch through the same data router swingbot-data uses, but offline only:
    # saved CSVs and the events file, each cut to the moment the news broke.
    router = Router(DataConfig(
        sources={"prices": "csv", "news": "events", "filings": "events", "fundamentals": "nse_results"},
        prices_dir=Path(args.with_technicals or "."), events_file=Path(args.events), cache_prices=False,
    ))
    mood_router = None
    if args.with_mood:
        mood_router = Router(DataConfig(sources={"prices": "csv"}, prices_dir=Path(args.with_mood),
                                        cache_prices=False))
    sections = (["technicals"] if args.with_technicals else []) + (
        ["news", "filings", "fundamentals"] if args.with_context else [])

    done: set[str] = set()
    if args.views and Path(args.views).exists():
        done = set(RecordedSwarm(args.views).views)
    todo = [e for e in _load(args.events, args.cutoff) if e.event_id not in done]
    if args.until:
        until = pd.Timestamp(args.until, tz=IST)
        todo = [e for e in todo if e.published_at < until]
    todo = _same_day_batch(todo, args.limit)

    lines = ["# Swarm brief", "", SYSTEM_PROMPT, "", "## Personas", ""]
    lines += [f"- **{p.name}**: {p.description}" for p in DEFAULT_PERSONAS]
    if mood_router:
        from .regime import MOOD_PROMPT
        lines += ["", "## Market mood", "", MOOD_PROMPT]
    if memory:
        lines += ["", "## Memory", "", MEMORY_PROMPT]
    if todo:
        lines += ["", "## Time rule", "", TIME_RULE.format(day=_ist_day(todo[0]).isoformat())]
    lines += ["", f"## Events ({len(todo)} in this batch)", ""]
    for e in todo:
        lines += [
            f"### {e.event_id} · {e.ticker} · {e.published_at.isoformat()}",
            f"Headline: {e.headline}",
        ]
        if e.body:
            lines.append(f"Details: {e.body}")
        if mood_router:
            from .regime import from_router
            lines += ["", from_router(mood_router, e.published_at).text()]
        for section in sections:
            lines += ["", router.text(section, e.ticker, e.published_at, exclude_event=e.event_id)]
        if memory:
            lines += ["", brief_section(memory, e, k=args.lessons_k)]
        lines.append("")
    if sections:
        lines += [ANALYST_PROMPT, ""]
    lines += [DEBATE_PROMPT, ""]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"{len(todo)} events written to {out} ({len(done)} already have views)")
    return 0
