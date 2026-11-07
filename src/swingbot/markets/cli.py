"""swingbot-markets: market profiles, run namespaces, halts, and a read-only Alpaca check.

    swingbot-markets profiles
    swingbot-markets new-run us 2026-11-us-paper     # runs/markets/us/<run-id>/manifest.json
    swingbot-markets halt us --reason "alpaca outage" # or `halt all`
    swingbot-markets resume us
    swingbot-markets status
    swingbot-markets alpaca-check                     # GET account and positions; sends nothing

No command here places an order.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .control import RUNS_ROOT, STATE_ROOT, RunSpace, halt, halted, resume
from .profiles import PROFILES


def _scope(market: str) -> str | None:
    return None if market == "all" else market


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-markets", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    roots = argparse.ArgumentParser(add_help=False)
    roots.add_argument("--runs-root", type=Path, default=RUNS_ROOT)
    roots.add_argument("--state-root", type=Path, default=STATE_ROOT)
    broker = argparse.ArgumentParser(add_help=False)
    broker.add_argument("--broker-config", type=Path, default=None)

    sub.add_parser("profiles")
    p = sub.add_parser("new-run", parents=[roots])
    p.add_argument("market")
    p.add_argument("run_id")
    p.add_argument("--config", default="{}", help="the run's frozen config, as JSON")
    p = sub.add_parser("halt", parents=[roots])
    p.add_argument("market", help="nse, us or all")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("resume", parents=[roots])
    p.add_argument("market", help="nse, us or all")
    sub.add_parser("status", parents=[roots, broker])
    sub.add_parser("alpaca-check", parents=[broker])
    args = ap.parse_args(argv)

    try:
        return _run(args)
    except (FileExistsError, FileNotFoundError, KeyError, ValueError, RuntimeError) as e:
        print(f"swingbot-markets: {e}", file=sys.stderr)
        return 1


def _run(args) -> int:
    from ..broker.alpaca import AlpacaAPI, AlpacaBroker, AlpacaConfig

    if args.cmd == "profiles":
        for p in PROFILES.values():
            close = f"{p.session_close:%H:%M}" + (f" ({p.early_close:%H:%M} early)" if p.early_close else "")
            print(f"{p.key:4} {p.name:13} {p.currency} {p.timezone}  {p.session_open:%H:%M}-{close}  "
                  f"benchmark {p.benchmark}  broker {p.broker}")
    elif args.cmd == "new-run":
        space = RunSpace.create(args.market, args.run_id, args.runs_root, args.state_root, json.loads(args.config))
        print(f"created {space.run_dir}")
    elif args.cmd == "halt":
        print(f"halt file at {halt(_scope(args.market), args.reason, args.state_root)}")
    elif args.cmd == "resume":
        print("halt removed" if resume(_scope(args.market), args.state_root) else "no halt file was there")
    elif args.cmd == "status":
        for key in PROFILES:
            reason = halted(key, args.state_root)
            print(f"{key}: HALTED ({reason})" if reason else f"{key}: running")
        cfg = AlpacaConfig.load(args.broker_config, required=False)
        print(f"alpaca: {'enabled' if cfg.enabled else 'disabled'}, {cfg.mode.value}")
    elif args.cmd == "alpaca-check":
        cfg = AlpacaConfig.load(args.broker_config, required=False)
        api = AlpacaAPI.from_env(cfg)
        acct = api.call("GET", "/v2/account")
        print(f"account {acct.get('status')} {acct.get('currency')}: cash {acct.get('cash')}, "
              f"buying power {acct.get('buying_power')}")
        b = AlpacaBroker(api)
        for line in b.reconcile() or ["positions match the book"]:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
