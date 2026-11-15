"""swingbot-markets: market profiles, run namespaces, halts, and a read-only Alpaca check.

    swingbot-markets profiles
    swingbot-markets new-run us 2026-11-us-paper     # runs/markets/us/<run-id>/manifest.json
    swingbot-markets halt us --reason "alpaca outage" # or `halt all`
    swingbot-markets resume us
    swingbot-markets status
    swingbot-markets alpaca-check                     # GET account and positions; sends nothing
    swingbot-markets alpaca-smoke --phase quiet       # phase 2 checks on the paper account (1 SPY share)
    swingbot-markets trials                           # phase 3: NSE and US paper trials, one report
    swingbot-markets memory update --market nse       # step 7: episodes and lessons from the booked months

Only alpaca-smoke places orders: paper only, 1 SPY share, cancelled or sold back.
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
    p = sub.add_parser("trials", help="phase 3: both paper trials, one report")
    p.add_argument("--nse-ledger", type=Path, default=Path("data/state/momentum_trial.json"))
    p.add_argument("--us-ref", default="origin/us-trial-state", help="git ref holding the US trial's state")
    p.add_argument("--out", type=Path, default=Path("runs/markets/trials.md"))
    mem = sub.add_parser("memory", help="step 7: episodes, mistakes and market-scoped lessons")
    msub = mem.add_subparsers(dest="mcmd", required=True)
    p = msub.add_parser("update", parents=[roots])
    p.add_argument("--market", choices=["nse", "us"], required=True)
    p.add_argument("--nse-ledger", type=Path, default=Path("data/state/momentum_trial.json"))
    p = msub.add_parser("show", parents=[roots])
    p.add_argument("--market", choices=["nse", "us"], required=True)
    p = msub.add_parser("classify", parents=[roots])
    p.add_argument("episode")
    p.add_argument("--mistake", required=True, help="a Mistake name, e.g. REGIME or FORECAST")
    p.add_argument("--evidence", required=True)
    p.add_argument("--by", required=True)
    p = msub.add_parser("propose", parents=[roots])
    p.add_argument("--scope", choices=["nse", "us"], required=True)
    p.add_argument("--level", choices=["market", "strategy"], required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--evidence", nargs="*", default=[])
    p.add_argument("--confidence", choices=["low", "medium", "high"], required=True)
    p.add_argument("--by", required=True)
    p = msub.add_parser("decide", parents=[roots])
    p.add_argument("--scope", choices=["nse", "us"], required=True)
    p.add_argument("lesson")
    p.add_argument("action", choices=["approve", "reject", "retire"])
    p.add_argument("--by", required=True)
    p.add_argument("--why", required=True)
    sk = sub.add_parser("skills", help="step 8: versioned skills per market, replay and approval gates")
    ssub = sk.add_subparsers(dest="scmd", required=True)
    p = ssub.add_parser("list", parents=[roots])
    p.add_argument("--market", choices=["nse", "us"], required=True)
    p = ssub.add_parser("propose", parents=[roots])
    p.add_argument("--market", choices=["nse", "us"], required=True)
    p.add_argument("--skill", required=True, help="e.g. us-momentum")
    p.add_argument("--set", nargs="+", required=True, metavar="KEY=VALUE", help="e.g. lookback=6 trend_gate=true")
    p.add_argument("--reason", required=True)
    p.add_argument("--evidence", nargs="+", required=True)
    p.add_argument("--owner", required=True)
    p = ssub.add_parser("generate", parents=[roots])
    p.add_argument("--market", choices=["us"], required=True)
    p.add_argument("--study-dir", type=Path, default=None, help="default: <state>/markets/us/research")
    p = ssub.add_parser("replay", parents=[roots])
    p.add_argument("--market", choices=["nse", "us"], required=True)
    p.add_argument("--panel", type=Path, default=Path("data/raw/us_panel"))
    p.add_argument("--data", type=Path, default=Path("data/raw/eod2_data/daily"), help="NSE: eod2_data's daily/")
    p = ssub.add_parser("decide", parents=[roots])
    p.add_argument("--market", choices=["nse", "us"], required=True)
    p.add_argument("skill", help="e.g. us-momentum@v2")
    p.add_argument("action", choices=["shadow", "approve", "reject", "retire"])
    p.add_argument("--by", required=True)
    p.add_argument("--why", required=True)
    p = sub.add_parser("alpaca-smoke", parents=[broker])
    p.add_argument("--phase", choices=["quiet", "fill"], required=True)
    p.add_argument("--out", type=Path, default=None, help="default: <state>/markets/us/smoke")
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
    elif args.cmd == "trials":
        from .trials import from_nse_ledger, from_us_state, report, us_state_from_git
        nse = from_nse_ledger(json.loads(args.nse_ledger.read_text())) if args.nse_ledger.exists() else []
        text = report(nse, from_us_state(us_state_from_git(args.us_ref)))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(text)
    elif args.cmd == "memory":
        from .memory import Memory, Mistake, show
        from .trials import from_nse_ledger, from_us_state
        m = Memory(args.state_root)
        if args.mcmd == "update":
            if args.market == "nse":
                rows = from_nse_ledger(json.loads(args.nse_ledger.read_text())) if args.nse_ledger.exists() else []
            else:
                rows = from_us_state(args.state_root)
            print(f"{args.market}: {m.update(args.market, rows)} new episode records from {len(rows)} booked months")
        elif args.mcmd == "show":
            print(show(m, args.market))
        elif args.mcmd == "classify":
            m.classify(args.episode, Mistake[args.mistake.upper()], args.evidence, args.by)
            print(f"{args.episode}: {Mistake[args.mistake.upper()].value} recorded")
        elif args.mcmd == "propose":
            print(m.propose(args.scope, args.level, args.text, args.evidence, args.confidence, args.by))
        else:
            m.decide(args.scope, args.lesson, args.action, args.by, args.why)
            print(f"{args.lesson}: {args.action}d by {args.by}")
    elif args.cmd == "skills":
        from .skills import Skills, show
        sk = Skills(args.state_root)
        if args.scmd == "list":
            print(show(sk, args.market))
        elif args.scmd == "propose":
            params = {}
            for kv in args.set:
                k, _, v = kv.partition("=")
                params[k] = {"true": True, "false": False}.get(v.lower(), float(v) if "." in v else
                                                              (int(v) if v.lstrip("-").isdigit() else v))
            print(sk.propose(args.market, args.skill, params, args.reason, args.evidence, args.owner))
        elif args.scmd == "generate":
            d = args.study_dir or args.state_root / "markets" / "us" / "research"
            sha = json.loads((d / "panel-manifest.json").read_text())["sha256"][:16]
            made = sk.generate_from_study("us", d / "study.json", sha)
            print(f"{len(made)} new candidate(s): {', '.join(made) or 'none'}")
        elif args.scmd == "replay":
            if args.market == "nse":
                import subprocess

                from ..momentum import load
                from .skills import NseMembers
                daily, val = load(args.data)
                members = NseMembers(daily, val)
                sha = "eod2-" + subprocess.run(["git", "-C", str(args.data.parent), "rev-parse", "--short=12", "HEAD"],
                                               capture_output=True, text=True, check=True).stdout.strip()
            else:
                from ..us_research import Membership, load_panel
                daily, manifest = load_panel(args.panel)
                members = Membership.from_json(json.loads((args.panel / "membership.json").read_text()))
                sha = manifest["sha256"][:16]
            for key, v in sk.all(args.market).items():
                if v["status"] == "candidate" and not any(r["panel"] == sha for r in v["replays"]):
                    r = sk.replay(args.market, key, daily, members, sha)
                    print(f"{key}: {r['verdict']} {'; '.join(r['why'])}")
            print(show(sk, args.market))
        else:
            sk.decide(args.market, args.skill, args.action, args.by, args.why)
            print(f"{args.skill}: {args.action} by {args.by}")
    elif args.cmd == "alpaca-smoke":
        from datetime import UTC, datetime

        from ..broker.alpaca_smoke import run_smoke
        cfg = AlpacaConfig.load(args.broker_config, required=False)
        res = run_smoke(AlpacaAPI.from_env(cfg), args.phase, datetime.now(UTC),
                        args.out or cfg.state_dir / "smoke")
        if res.get("skipped"):
            print(f"{args.phase}: skipped ({res['skipped']})")
            return 0
        print((args.out or cfg.state_dir / "smoke").joinpath(f"{args.phase}.md").read_text())
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
