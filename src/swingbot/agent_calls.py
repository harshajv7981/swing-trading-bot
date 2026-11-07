"""The trading-agents skill's decision log, and its forward score.

The skill (`.claude/skills/trading-agents/`) plays TradingAgents' roles inside
Claude Code, so a decision costs no API money. Each finished decision is one
line in `runs/trading-agents/decisions.jsonl`, written only through `record`,
which checks the rating and refuses a past date or a second call on the same
ticker and day. `score` settles every call whose holding window has traded, the
way TradingAgents does: from the first close after the decision day, over 5
sessions, against the Nifty 50. The plan is fixed in
docs/research/trading-agents-prereg.md.

    swingbot-agent-calls record runs/trading-agents/today/INFY.json
    swingbot-agent-calls score                  # every call, settled or pending, and the summary
    swingbot-agent-calls score --ticker INFY    # one stock's past calls (the Portfolio Manager's memory)
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Callable

import pandas as pd

from .models import IST

LOG = Path("runs/trading-agents/decisions.jsonl")
HOLD_DAYS = 5
BENCHMARK = "NIFTY50"
# TradingAgents' 5-tier scale, most bullish first. REVIEW is a run whose rating
# could not be read: logged, never scored as a Hold.
DIRECTION = {"Buy": 1, "Overweight": 1, "Hold": 0, "Underweight": -1, "Sell": -1}
REVIEW = "REVIEW"
REQUIRED = ("ticker", "as_of", "rating", "rationale")

Closes = Callable[[str], pd.Series]


def load(path: Path = LOG) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _day(as_of: str) -> pd.Timestamp:
    return pd.Timestamp(as_of[:10])


def record(decision: dict, path: Path = LOG, now: pd.Timestamp | None = None) -> dict:
    """Check one decision and append it to the log. Raises ValueError on a bad one."""
    missing = [k for k in REQUIRED if not decision.get(k)]
    if missing:
        raise ValueError(f"the decision has no {', '.join(missing)}")
    d = dict(decision)
    d["ticker"] = d["ticker"].upper().removesuffix(".NS")
    rating = str(d["rating"]).strip()
    rating = REVIEW if rating.upper() == REVIEW else rating.capitalize()
    if rating not in DIRECTION and rating != REVIEW:
        raise ValueError(f"rating {d['rating']!r} is not one of {', '.join(DIRECTION)} or {REVIEW}")
    d["rating"] = rating
    now = (now or pd.Timestamp.now(tz=IST)).tz_convert(IST)
    if _day(d["as_of"]) != now.tz_localize(None).normalize():
        raise ValueError(f"as_of {d['as_of']} is not today ({now:%Y-%m-%d}): the test is forward only, "
                         "because a model may already know what happened after a past date")
    if any(x["ticker"] == d["ticker"] and x["as_of"][:10] == d["as_of"][:10] for x in load(path)):
        raise ValueError(f"{d['ticker']} already has a call for {d['as_of'][:10]}; the first one stands")
    d["recorded_at"] = f"{now:%Y-%m-%d %H:%M}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(d) + "\n")
    return d


def settle(decision: dict, stock: pd.Series, bench: pd.Series) -> dict | None:
    """The call's 5-session outcome, or None while its window has not traded yet."""
    after = stock[stock.index > _day(decision["as_of"])]
    if len(after) <= HOLD_DAYS:
        return None
    start, end = after.index[0], after.index[HOLD_DAYS]
    b0, b1 = bench[bench.index <= start], bench[bench.index <= end]
    if b0.empty or b1.index[-1] < end:
        return None   # the benchmark has not reached the exit day yet
    raw = after.iloc[HOLD_DAYS] / after.iloc[0] - 1
    alpha = raw - (b1.iloc[-1] / b0.iloc[-1] - 1)
    direction = DIRECTION.get(decision["rating"], 0)
    return {"entry_day": f"{start:%Y-%m-%d}", "exit_day": f"{end:%Y-%m-%d}", "raw": raw, "alpha": alpha,
            "signed_alpha": direction * alpha if direction else None}


def score(decisions: list[dict], closes: Closes) -> tuple[list[dict], dict]:
    """Every decision with its outcome (or why it has none), and the summary over the directional ones."""
    cache: dict[str, pd.Series | str] = {}

    def series(t):
        if t not in cache:
            try:
                cache[t] = closes(t)
            except Exception as e:   # one stock's missing prices must not stop the rest
                cache[t] = f"no prices: {e}"
        return cache[t]

    bench, rows = series(BENCHMARK), []
    for d in decisions:
        s = series(d["ticker"])
        if isinstance(s, str) or isinstance(bench, str):
            rows.append({**d, "status": s if isinstance(s, str) else f"benchmark {bench}"})
            continue
        out = settle(d, s, bench)
        rows.append({**d, "status": "settled" if out else "pending", **(out or {})})
    signed = [r["signed_alpha"] for r in rows if r["status"] == "settled" and r.get("signed_alpha") is not None]
    n = len(signed)
    mean = sum(signed) / n if n else float("nan")
    sd = math.sqrt(sum((x - mean) ** 2 for x in signed) / (n - 1)) if n > 1 else float("nan")
    summary = {
        "calls": len(rows), "settled": sum(r["status"] == "settled" for r in rows),
        "pending": sum(r["status"] == "pending" for r in rows),
        "review": sum(r["rating"] == REVIEW for r in rows),
        "directional": n, "hit_rate": sum(x > 0 for x in signed) / n if n else float("nan"),
        "mean_signed_alpha": mean, "t": mean / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan"),
    }
    return rows, summary


def router_closes(t: str, now: pd.Timestamp | None = None) -> pd.Series:
    from .data import Router
    from .technicals import bars_known_at

    now = now or pd.Timestamp.now(tz=IST)
    _, bars = Router().fetch("prices", t, now)
    return bars_known_at(bars, now)["close"]   # a session still trading has a live bar, not a close


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2%}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-agent-calls", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=LOG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="check one decision JSON and append it to the log")
    r.add_argument("decision", type=Path)
    s = sub.add_parser("score", help="settle every call whose 5 sessions have traded")
    s.add_argument("--ticker", help="only this stock's calls")
    args = ap.parse_args(argv)

    if args.cmd == "record":
        try:
            d = record(json.loads(args.decision.read_text()), args.log)
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        print(f"recorded {d['ticker']} {d['rating']} as of {d['as_of']} in {args.log}")
        return 0

    decisions = [d for d in load(args.log) if not args.ticker or d["ticker"] == args.ticker.upper()]
    if not decisions:
        print(f"no calls{' for ' + args.ticker.upper() if args.ticker else ''} in {args.log} yet")
        return 0
    rows, sm = score(decisions, router_closes)
    for x in rows:
        line = f"{x['as_of'][:10]}  {x['ticker']:<11} {x['rating']:<11} "
        if x["status"] == "settled":
            line += (f"{x['entry_day']}..{x['exit_day']}  raw {_pct(x['raw'])}  vs Nifty {_pct(x['alpha'])}"
                     f"  in the call's direction {_pct(x['signed_alpha'])}")
        else:
            line += x["status"]
        print(line)
    hit = f"{sm['hit_rate']:.0%}" if sm["directional"] else "n/a"
    t = f"{sm['t']:.2f}" if not math.isnan(sm["t"]) else "n/a"
    print(f"{sm['calls']} calls: {sm['settled']} settled, {sm['pending']} pending, {sm['review']} unreadable "
          f"(REVIEW). Directional and settled: {sm['directional']}; hit rate {hit}; mean alpha in the "
          f"call's direction {_pct(sm['mean_signed_alpha'])}, t {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
