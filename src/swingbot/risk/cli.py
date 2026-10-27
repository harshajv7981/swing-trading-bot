"""swingbot-risk: plan the exit, size the position, and gate the order.

Nothing here talks to a broker. Each step writes a small JSON file the next one
reads, and the gate records every verdict with the order's fingerprint.

    swingbot-risk mood --out runs/mood.json          # bullish, neutral or defensive market
    swingbot-risk plan RELIANCE --stop atr:2 --target 2R --out runs/plan.json
    swingbot-risk odds runs/plan.json --hold 10      # the Time Machine
    swingbot-risk size runs/plan.json --portfolio portfolio.json --mood runs/mood.json --out runs/order.json
    swingbot-risk check runs/order.json --portfolio portfolio.json --results-date 2026-11-14
    swingbot-risk ticket runs/order.json --out runs/ticket.json   # paper only
    swingbot-risk trail RELIANCE --entry-date 2026-10-20 --entry 1450 --initial-stop 1400 --stop 1400
    swingbot-risk kill-switch                     # status
    swingbot-risk kill-switch --reset --by Harsha # Harsha only, by hand

Exit codes: 0 done / PASS, 1 refused / BLOCK, 2 bad input or no data.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from ..data.router import DataConfig, DataError, Router, parse_as_of
from ..technicals import bars_known_at
from .calendar import NSECalendar
from .config import RiskConfig
from .exits import ExitPlan, PlanRefused, plan_exit, trail_stop
from .gate import DEFAULT_STATE_DIR, KillSwitch, check_order
from .portfolio import Order, Portfolio, load_sectors
from .sizing import SizingRefused, size_position
from .ticket import TicketRefused, latest_verdict, make_ticket
from .ticket import describe as describe_ticket
from .timemachine import NotEnoughHistory
from .timemachine import run as run_timemachine
from .. import regime


def _bars(ticker: str, as_of: pd.Timestamp, data_config: str | None) -> pd.DataFrame:
    _, bars = Router(DataConfig.load(data_config)).fetch("prices", ticker, as_of)
    return bars_known_at(bars, as_of)


def _write(obj: dict, out: str | None) -> None:
    text = json.dumps(obj, indent=2)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n")
        print(f"wrote {out}")
    else:
        print(text)


def _mood(path: str | None) -> dict | None:
    return json.loads(Path(path).read_text()) if path else None


def cmd_mood(args, config: RiskConfig) -> int:
    mood = regime.from_router(Router(DataConfig.load(args.data_config)), parse_as_of(args.as_of))
    print(mood.text())
    if mood.verdict == "defensive":
        print(f"R11: new entries risk {config.risk_pct_for('defensive'):g}% of equity instead of {config.risk_pct:g}%")
    if args.out:
        _write(mood.to_json(), args.out)
    return 0


def cmd_plan(args, config: RiskConfig) -> int:
    as_of = parse_as_of(args.as_of)
    plan = plan_exit(args.ticker, _bars(args.ticker, as_of, args.data_config), args.entry,
                     args.stop, args.target, config)
    print(f"{plan.ticker}: entry {plan.entry:,.2f}, stop {plan.stop:,.2f} ({plan.stop_rule}), "
          f"target {plan.target:,.2f} ({plan.target_rule}); risk {plan.risk_per_share:,.2f}/share, "
          f"reward:risk {plan.reward_risk:.2f}, ATR {plan.atr:,.2f}, bars to {plan.as_of}")
    _write(plan.to_json(), args.out)
    return 0


def cmd_odds(args, config: RiskConfig) -> int:
    plan = ExitPlan.from_json(json.loads(Path(args.plan).read_text()))
    bars = _bars(plan.ticker, parse_as_of(plan.as_of), args.data_config)
    report = run_timemachine(plan, bars, hold=args.hold, replays=args.replays, seed=args.seed,
                             lookback=args.lookback, atr_period=config.atr_period)
    print(report.text())
    if args.out:
        _write(report.to_json(), args.out)
    return 0


def cmd_size(args, config: RiskConfig) -> int:
    plan = ExitPlan.from_json(json.loads(Path(args.plan).read_text()))
    sector = args.sector or load_sectors().get(plan.ticker, "")
    s = size_position(plan, Portfolio.load(args.portfolio), config, sector, mood=_mood(args.mood))
    if not args.mood:
        print("note: market mood not checked; pass --mood runs/risk/mood.json", file=sys.stderr)
    elif s.risk_pct < config.risk_pct:
        print(f"market mood is defensive: risking {s.risk_pct:g}% of equity instead of {config.risk_pct:g}%")
    print(f"buy {s.order.qty} {plan.ticker} @ {plan.entry:,.2f} = {s.order.qty * plan.entry:,.0f}; "
          f"decided by: {s.binding}; loss at stop {s.loss_at_stop:,.0f} "
          f"({s.loss_with_gap:,.0f} with the gap allowance)")
    print("shares each limit allows: " + ", ".join(f"{k} {v}" for k, v in s.limits.items()))
    _write(s.order.to_json(), args.out)
    return 0


def cmd_check(args, config: RiskConfig) -> int:
    order = Order.from_json(json.loads(Path(args.order).read_text()))
    if not order.sector:
        order = Order.from_json({**order.to_json(), "sector": load_sectors().get(order.ticker, "")})
    as_of = parse_as_of(args.as_of)
    switch = KillSwitch(args.state_dir)
    bars = None
    if order.side == "buy":
        try:
            bars = _bars(order.ticker, as_of, args.data_config)
        except DataError as e:
            print(f"note: no price history for the trend filter ({e})", file=sys.stderr)
    verdict = check_order(
        order, Portfolio.load(args.portfolio), config, bars=bars, today=as_of.date(),
        results_date=date.fromisoformat(args.results_date) if args.results_date else None,
        calendar=NSECalendar.load(), kill_switch_latched=switch.latched, last_price=args.price,
        mood=_mood(args.mood),
    )
    if verdict.trip_kill_switch and not args.no_write:
        switch.latch(verdict.trip_kill_switch)
    print(verdict.text())
    if not args.no_write:
        log = Path(args.state_dir) / "risk_verdicts.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as fh:
            fh.write(json.dumps({"at": f"{as_of:%Y-%m-%d %H:%M}", **verdict.to_json()}) + "\n")
    return 0 if verdict.passed else 1


def cmd_ticket(args, config: RiskConfig) -> int:
    order = Order.from_json(json.loads(Path(args.order).read_text()))
    verdict = latest_verdict(Path(args.state_dir) / "risk_verdicts.jsonl", order.fingerprint)
    ticket = make_ticket(order, verdict, parse_as_of(args.as_of).date(), config,
                         kill_switch_latched=KillSwitch(args.state_dir).latched)
    print(describe_ticket(ticket))
    _write(ticket, args.out)
    return 0


def cmd_trail(args, config: RiskConfig) -> int:
    bars = _bars(args.ticker, parse_as_of(args.as_of), args.data_config)
    new, why = trail_stop(bars, args.entry_date, args.entry, args.initial_stop, args.stop, config)
    moved = "moves to" if new > args.stop else "stays at"
    print(f"{args.ticker.upper()} stop {moved} {new:,.2f}: {why}")
    return 0


def cmd_kill_switch(args, config: RiskConfig) -> int:
    switch = KillSwitch(args.state_dir)
    if args.reset:
        if not args.by:
            print("a reset needs --by NAME: it is recorded", file=sys.stderr)
            return 2
        switch.reset(args.by)
        print(f"kill switch reset by {args.by}")
        return 0
    print(json.dumps(switch.state(), indent=2))
    return 1 if switch.latched else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-risk", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="risk limits TOML (default config/risk.toml)")
    p.add_argument("--data-config", help="data sources TOML (default config/data_sources.toml)")
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="kill switch and verdict log")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("mood", help="market mood: bullish, neutral or defensive (R11)")
    s.add_argument("--as-of", help="YYYY-MM-DD or 'YYYY-MM-DD HH:MM' IST; default now")
    s.add_argument("--out")

    s = sub.add_parser("plan", help="stop and target for a long entry")
    s.add_argument("ticker")
    s.add_argument("--entry", type=float, help="default: the last close known at --as-of")
    s.add_argument("--stop", default="atr:2", help="atr:K, pct:X, price:X or low:N (default atr:2)")
    s.add_argument("--target", default="2R", help="a multiple like 2R, or price:X (default 2R)")
    s.add_argument("--as-of", help="YYYY-MM-DD or 'YYYY-MM-DD HH:MM' IST; default now")
    s.add_argument("--out")

    s = sub.add_parser("odds", help="the Time Machine: odds of target, stop or neither over the hold")
    s.add_argument("plan")
    s.add_argument("--hold", type=int, default=10, help="sessions to hold at most (default 10)")
    s.add_argument("--replays", type=int, default=10_000)
    s.add_argument("--seed", type=int, default=19)
    s.add_argument("--lookback", type=int, default=500, help="past sessions to draw from (default 500)")
    s.add_argument("--out")

    s = sub.add_parser("size", help="whole shares for a plan")
    s.add_argument("plan")
    s.add_argument("--portfolio", required=True)
    s.add_argument("--sector", help="default: data/universe/sectors.csv")
    s.add_argument("--mood", help="a swingbot-risk mood file; defensive cuts the risk per trade (R11)")
    s.add_argument("--out")

    s = sub.add_parser("check", help="PASS or BLOCK one order")
    s.add_argument("order")
    s.add_argument("--portfolio", required=True)
    s.add_argument("--results-date", help="the company's next results date, YYYY-MM-DD")
    s.add_argument("--price", type=float, help="a fresh price; risk is measured from the higher of it and the entry")
    s.add_argument("--as-of")
    s.add_argument("--mood", help="a swingbot-risk mood file (default: the one the order was sized under)")
    s.add_argument("--no-write", action="store_true", help="do not log the verdict or latch the kill switch")

    s = sub.add_parser("ticket", help="the paper broker orders for a PASSed order (never sent)")
    s.add_argument("order")
    s.add_argument("--as-of", help="the day of the PASS; default today")
    s.add_argument("--out")

    s = sub.add_parser("trail", help="the trailing stop for an open position (R7)")
    s.add_argument("ticker")
    s.add_argument("--entry-date", required=True)
    s.add_argument("--entry", type=float, required=True)
    s.add_argument("--initial-stop", type=float, required=True)
    s.add_argument("--stop", type=float, required=True, help="the current stop")
    s.add_argument("--as-of")

    s = sub.add_parser("kill-switch", help="show, or reset by hand, the weekly kill switch")
    s.add_argument("--reset", action="store_true")
    s.add_argument("--by", help="who is resetting it")

    args = p.parse_args(argv)
    commands = {"mood": cmd_mood, "plan": cmd_plan, "odds": cmd_odds, "size": cmd_size, "check": cmd_check,
                "ticket": cmd_ticket, "trail": cmd_trail, "kill-switch": cmd_kill_switch}
    try:
        return commands[args.cmd](args, RiskConfig.load(args.config))
    except (PlanRefused, SizingRefused, TicketRefused, NotEnoughHistory) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except (DataError, FileNotFoundError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
