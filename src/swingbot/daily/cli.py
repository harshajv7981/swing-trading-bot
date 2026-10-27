"""swingbot-daily: the daily paper trading loop, in two halves around the swarm.

    swingbot-daily start     # before 09:15 IST: refresh, settle, account, kill switch, mood, brief
    (play the swarm on runs/daily/<date>/brief.md, writing views.jsonl there)
    swingbot-daily brief     # the next batch of today's news, until it says 0
    swingbot-daily finish    # signals, orders, risk gate, tickets, paper broker, report, Verifier
    swingbot-daily status    # what today's run has done so far
    swingbot-daily alerts --test   # send one test message on the [alerts] channel

The `daily-paper-loop` skill walks Claude Code through a whole day. Paper
only: nothing here talks to a real broker. Exit codes: 0 done, 1 a step
failed or found rule breaks, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import alerts
from .hooks import BLOCKED, FAILED, Step
from .loop import DailyConfig, Day, brief, finish, load_state, start


def _print_steps(steps: list[dict]) -> None:
    for s in steps:
        print("  " + Step(**s).line())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-daily", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="daily loop TOML (default config/daily.toml)")
    p.add_argument("--as-of", help="'YYYY-MM-DD HH:MM' IST, to replay a day; default now")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="first half: everything up to the swarm brief")
    sub.add_parser("brief", help="the next batch of today's news for the swarm")
    sub.add_parser("finish", help="second half: from the swarm's views to the report")
    sub.add_parser("status", help="today's day.json and finish.json")
    al = sub.add_parser("alerts", help="send the alerts not sent yet, or a test message")
    al.add_argument("--test", action="store_true", help="send one test message and nothing else")
    args = p.parse_args(argv)

    try:
        day = Day(args.as_of, DailyConfig.load(args.config))
        if args.cmd == "start":
            state = start(day)
            print(f"Paper trading day {day.as_of:%Y-%m-%d}, started {day.as_of:%H:%M} IST")
            _print_steps(state["steps"])
            for n in day.notes:
                print(f"  note     {n}")
            if state["entries"]:
                brief_step = state["steps"][-1]
                if day.path("brief.md").exists() and "nothing to trade" not in brief_step["detail"]:
                    print(f"\nNext: play the swarm on {day.path('brief.md')}, append views to "
                          f"{day.path('views.jsonl')}, then `swingbot-daily brief` until 0 are left, "
                          "then `swingbot-daily finish`.")
                else:
                    print("\nNext: `swingbot-daily finish` for the report.")
            else:
                print("\nNo new entries today: " + "; ".join(state["why_not"]) +
                      ".\nNext: `swingbot-daily finish` for the report and the Verifier.")
            return 1 if any(s["status"] == FAILED for s in state["steps"]) else 0
        if args.cmd == "brief":
            state = load_state(day)
            if not state["entries"]:
                print("No new entries today, so no brief: " + "; ".join(state["why_not"]))
                return 0
            print(brief(day, state).line())
            return 0
        if args.cmd == "finish":
            result = finish(day)
            print(day.path("summary.md").read_text())
            print(f"wrote {day.path('summary.md')}")
            if day.path("verify.md").exists() and any(
                    s["name"] == "trade verifier" and "need a lesson" in s["detail"] for s in result["steps"]):
                print(f"Next: play the Verifier on {day.path('verify.md')} (trade-verifier skill), "
                      "then `swingbot-memory learn --lessons <file>`.")
            broke = any(s["name"] == "ledger report" and s["status"] == BLOCKED for s in result["steps"])
            return 1 if broke or any(s["status"] == FAILED for s in result["steps"]) else 0
        if args.cmd == "alerts":
            cfg = day.config.alerts
            if args.test:
                send = alerts.sender(cfg)
                if send is None:
                    print("error: [alerts] channel is \"none\" in the daily config; set ntfy or telegram first",
                          file=sys.stderr)
                    return 2
                send(alerts.Alert("test", "Swing bot test", f"Alerts reach you via {cfg.channel}."))
                print(f"sent a test message via {cfg.channel}")
                return 0
            d = alerts.dispatch(day.ledger, day.state_dir, cfg, f"{day.as_of:%Y-%m-%d}")
            print(f"alerts via {cfg.channel}: {d.detail()}")
            for a, why in d.failed:
                print(f"  not sent: {a.line()} ({why})")
            return 1 if d.failed else 0
        if args.cmd == "status":
            for name in ("day.json", "finish.json"):
                if day.path(name).exists():
                    print(f"{name}:")
                    _print_steps(json.loads(day.path(name).read_text())["steps"])
                else:
                    print(f"{name}: not written yet")
            return 0
    except (FileNotFoundError, ValueError, KeyError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
