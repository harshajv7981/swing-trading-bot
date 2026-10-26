"""swingbot-backtest: run a swarm over historical news and score it."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .backtest import run_backtest
from .events import load_events
from .metrics import format_summary, summarise
from .prices import CSVPriceSource, YFinancePriceSource
from .swarm import build_engine


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--events", required=True, help="CSV or JSONL of news events")
    p.add_argument("--prices", help="directory of <TICKER>.csv daily bars")
    p.add_argument("--yahoo", nargs=2, metavar=("START", "END"),
                   help="fetch NSE bars from Yahoo instead, e.g. --yahoo 2025-01-01 2026-10-01")
    p.add_argument("--engine", default="lexicon", choices=["lexicon", "claude", "mirofish"])
    p.add_argument("--model", help="Claude model for --engine claude")
    p.add_argument("--rounds", type=int, default=2, help="swarm interaction rounds")
    p.add_argument("--horizon", default="72h", help="signal horizon, e.g. 48h or 72h")
    p.add_argument("--benchmark", help="ticker of a benchmark series, e.g. NIFTY50 (CSV) or ^NSEI (Yahoo)")
    p.add_argument("--cost-bps", type=float, default=25.0, help="round-trip cost per trade in bps")
    p.add_argument("--out", default="runs", help="directory for results")
    args = p.parse_args(argv)

    if bool(args.prices) == bool(args.yahoo):
        p.error("pass exactly one of --prices or --yahoo")
    prices = CSVPriceSource(args.prices) if args.prices else YFinancePriceSource(*args.yahoo)

    events = load_events(args.events)
    engine = build_engine(args.engine, rounds=args.rounds, horizon=args.horizon, model=args.model)
    results = run_backtest(events, engine, prices, benchmark=args.benchmark, cost_bps=args.cost_bps)
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
