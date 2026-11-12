"""The large-cap TradingAgents test: its decision log, its forward score, and the API run.

Two ways to make a call, both with this repo's data tools and TradingAgents' roles:

* Skill (no API key): the `trading-agents` skill (`.claude/skills/trading-agents/`)
  plays the roles inside Claude Code and hands the finished decision to `record`.
* API (needs ANTHROPIC_API_KEY in .env): `run` fetches the same data, asks the
  model for each role in its own call, in TradingAgents' graph order, and
  records the decision itself.

Each finished decision is one line in `runs/trading-agents/decisions.jsonl`,
written only through `record`, which checks the rating and refuses a past date
or a second call on the same ticker and day. `score` settles every call whose
holding window has traded, the way TradingAgents does: from the first close
after the decision day, over 5 sessions, against the Nifty 50. The plan is
fixed in docs/research/trading-agents-prereg.md.

    swingbot-agent-calls record runs/trading-agents/today/INFY.json
    swingbot-agent-calls run                    # the watchlist, today, through the API
    swingbot-agent-calls run INFY               # one stock (logged beside the test's calls)
    swingbot-agent-calls score                  # every call, settled or pending, and the summary
    swingbot-agent-calls score --ticker INFY    # one stock's past calls (the Portfolio Manager's memory)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Callable

import pandas as pd

from .agents_trial import KEY_ENV, MAX_USD, MODEL, call_cost
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


# ---------------------------------------------------------------- the API run

# The prereg's watchlist, in its order. Changing it starts a new test.
WATCHLIST = ("RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "BHARTIARTL", "SUNPHARMA",
             "MARUTI")
SOCIAL_GAP = "Social sentiment: NOT_CONFIGURED"
GAP_WORDS = ("NO_DATA", "WITHHELD", "DATA_UNAVAILABLE", "NOT_CONFIGURED")
MAX_TOKENS = 8000   # per role, thinking included; a role's note is a page or two

# What every role is told. The skill's rules 2 and 3, for a model that has no tools.
SYSTEM = """You are one agent in TradingAgents, a multi-agent trading research framework, working on \
{ticker}, a stock on India's NSE. Prices are in rupees. Today is {as_of} IST.

Rules:
- Every number you write must come from the material you are given. Use no price, ratio, date or \
event from memory.
- A line starting NO_DATA, WITHHELD, DATA_UNAVAILABLE or NOT_CONFIGURED is a gap. Report it as a \
gap. Never estimate around it, and never read it as neutral.
- Do only your own role's job, from only what you are given.
- This is a paper forward test. Nothing you write places an order.

Your role: {role}"""

REPORTS = ("market", "sentiment", "news_report", "fundamentals_report")   # the four analysts' notes
# One entry per model call, in TradingAgents' graph order: the key its text is kept under, the role's
# brief (from the skill's steps), and what the role is shown (data pack keys, or earlier roles' keys).
ROLES = (
    ("market", "Market Analyst. Pick the indicators that matter here (trend: EMA10, SMA50, SMA200; "
     "momentum: RSI, MACD; volatility: ATR, Bollinger; volume: VWMA) without repeating each other, and "
     "write a detailed report of the trend, momentum, volatility (quote ATR14: it sets the stop) and "
     "volume, ending in a short Markdown table of the key points. If the last close in the data is "
     "older than today, say so: it is a gap, not today's picture. Report what the data shows; you "
     "don't decide the trade.", ("technicals", "prices")),
    ("sentiment", "Sentiment Analyst. There is no Reddit or StockTwits data: write `" + SOCIAL_GAP +
     "` as a gap. From the news items, read the tone of the coverage: separate events (a contract, a "
     "downgrade) from opinion, say how many items there were, and lower your confidence plainly when "
     "there are few.", ("news",)),
    ("news_report", "News Analyst. What is new, when it broke (timestamps matter), what the market may not "
     "have priced yet, and the macro backdrop the market mood shows, naming any mood check that was "
     "unavailable. End with a Markdown table.", ("news", "filings", "mood")),
    ("fundamentals_report", "Fundamentals Analyst. Report the latest results filing, or the present-day "
     "ratios when that is the only source; say which it was. End with a Markdown table.",
     ("fundamentals",)),
    ("bull", "Bull Researcher. Build an evidence-based case for owning the stock from the four "
     "analyst reports: growth, competitive position, positive signals, and answers to what a bear "
     "would say. Speak conversationally and cite the reports, not new data.",
     REPORTS),
    ("bear", "Bear Researcher. Argue against owning the stock: risks, weak spots, negative signals, "
     "and direct answers to the bull's points. Speak conversationally and cite the reports, not new "
     "data.", (*REPORTS, "bull")),
    ("research_plan", "Research Manager. Judge the bull and bear debate and give an investment plan "
     "for the trader: a rating on the 5-tier scale (Buy, Overweight, Hold, Underweight, Sell), the "
     "reasons, and the strategy. Don't default to Hold because both sides made points: pick the "
     "stronger case.", (*REPORTS, "bull", "bear")),
    ("trader", "Trader. Turn the plan into a proposal. Start with `Action: Buy`, `Action: Hold` or "
     "`Action: Sell` on its own line (Overweight counts as Buy, Underweight as Sell), then the "
     "reasoning, then the entry and the stop as rupee prices taken from the market report (last "
     "close, support and resistance, ATR), never a percentage. Give the stop distance as a multiple "
     "of ATR14.", (*REPORTS, "research_plan")),
    ("aggressive", "Aggressive Risk Analyst. Argue for taking the upside in the trader's proposal and "
     "challenge caution.", (*REPORTS, "trader")),
    ("conservative", "Conservative Risk Analyst. Protect capital: press on what could go wrong with "
     "the trader's proposal, and answer the aggressive analyst's points.",
     (*REPORTS, "trader", "aggressive")),
    ("neutral", "Neutral Risk Analyst. Weigh the aggressive and conservative analysts, say where each "
     "overreaches, and answer their last points.",
     (*REPORTS, "trader", "aggressive", "conservative")),
    ("final", "Portfolio Manager. Synthesise the risk debate into the final decision, weighing this "
     "stock's past calls and what followed them. The first line must be `Rating: X`, where X is one "
     "of Buy, Overweight, Hold, Underweight, Sell; if you can't commit to one, write `Rating: REVIEW`. "
     "Then the rationale and what would change the call. End with exactly these three lines: "
     "`Entry: <rupee price or none>`, `Stop: <rupee price or none>`, `Rationale: <one or two sentences>`.",
     ("research_plan", "trader", "aggressive", "conservative", "neutral", "past_calls")),
)
TITLES = {"technicals": "Technical indicators", "prices": "Daily bars", "news": "News, last 7 days",
          "filings": "Exchange filings, last 14 days", "mood": "Market mood",
          "fundamentals": "Fundamentals", "market": "Market Analyst's report",
          "sentiment": "Sentiment Analyst's report", "news_report": "News Analyst's report",
          "fundamentals_report": "Fundamentals Analyst's report", "bull": "Bull Researcher",
          "bear": "Bear Researcher", "research_plan": "Research Manager's plan",
          "trader": "Trader's proposal", "aggressive": "Aggressive Risk Analyst",
          "conservative": "Conservative Risk Analyst", "neutral": "Neutral Risk Analyst",
          "past_calls": "This stock's past calls in the test, and what followed"}

Complete = Callable[[str, str], tuple[str, int, int]]   # (system, user) -> (text, tokens in, tokens out)


def api_key(env_file: Path = Path(".env")) -> str | None:
    """The key from the environment, else from .env in this folder, where `swingbot-agents run` reads it."""
    if os.environ.get(KEY_ENV):
        return os.environ[KEY_ENV]
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, sep, value = line.strip().removeprefix("export ").partition("=")
            if sep and name.strip() == KEY_ENV and value.strip():
                return value.strip().strip("'\"")
    return None


def anthropic_complete(key: str, model: str = MODEL) -> Complete:
    """One Messages API call per role. Haiku 5.5 thinks by itself; it takes no temperature."""
    try:
        import anthropic
    except ImportError:
        raise SystemExit("the API run needs the anthropic package: pip install -e '.[api]'")
    client = anthropic.Anthropic(api_key=key)

    def complete(system: str, user: str) -> tuple[str, int, int]:
        r = client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system,
                                   messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        if r.stop_reason == "max_tokens":
            text += "\n\n(cut off at the length limit)"
        return text, r.usage.input_tokens, r.usage.output_tokens

    return complete


def data_pack(router, ticker: str, as_of: pd.Timestamp, mood: str, past: str) -> dict[str, str]:
    """What the skill fetches with swingbot-data, cut at `as_of`. No model calls."""
    return {
        "technicals": router.text("technicals", ticker, as_of),
        "prices": router.text("prices", ticker, as_of, rows=60),
        "news": router.text("news", ticker, as_of, days=7),
        "filings": router.text("filings", ticker, as_of, days=14),
        "fundamentals": router.text("fundamentals", ticker, as_of),
        "mood": mood,
        "past_calls": past,
    }


def past_calls(ticker: str, decisions: list[dict], closes: Closes) -> str:
    rows, _ = score([d for d in decisions if d["ticker"] == ticker], closes)
    return "\n".join(_line(x) for x in rows) or "No earlier calls on this stock."


def _number(label: str, text: str) -> float | None:
    m = re.search(rf"^\W*{label}\W*:\W*(?:Rs\.?|INR|₹)?\s*([\d,]+(?:\.\d+)?)", text, re.M | re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _word(label: str, text: str, choices) -> str | None:
    m = re.search(rf"^\W*{label}\W*:\W*({'|'.join(choices)})\b", text, re.M | re.I)
    return m.group(1).capitalize() if m else None


def decide(ticker: str, as_of: pd.Timestamp, pack: dict[str, str], complete: Complete,
           usage: dict | None = None) -> tuple[dict, dict]:
    """Every role in order, each shown only what TradingAgents gives it. Returns the decision and its usage.

    `usage` is added to as each call returns, so a caller that passes its own
    still knows what was spent when a later role fails."""
    said = dict(pack)                      # role outputs join the data pack under their own keys
    usage = usage if usage is not None else {}
    usage.update(llm_calls=0, tokens_in=0, tokens_out=0, usd=0.0)
    for key, brief, shown in ROLES:
        system = SYSTEM.format(ticker=ticker, as_of=f"{as_of:%Y-%m-%d %H:%M}", role=brief)
        text, t_in, t_out = complete(system, "\n\n".join(f"## {TITLES[k]}\n{said[k]}" for k in shown))
        said[key] = text
        usage["llm_calls"] += 1
        usage["tokens_in"] += t_in
        usage["tokens_out"] += t_out
        usage["usd"] += call_cost(MODEL, t_in, t_out)
    final = said["final"]
    rating = _word("Rating", final, [*DIRECTION, REVIEW]) or REVIEW
    rationale = re.search(r"^\W*Rationale\W*:\s*(.+)", final, re.M | re.I)
    gaps = [SOCIAL_GAP] + [line.strip()[:200] for k in ("technicals", "prices", "news", "filings",
                                                         "fundamentals", "mood")
                           for line in pack[k].splitlines() if line.strip().startswith(GAP_WORDS)]
    decision = {
        "ticker": ticker, "as_of": f"{as_of:%Y-%m-%d %H:%M}",
        "rating": REVIEW if rating.upper() == REVIEW else rating,
        "rationale": (rationale.group(1).strip() if rationale else final[:300]) or "no rationale given",
        "action": _word("Action", said["trader"], ("Buy", "Hold", "Sell")),
        "entry": _number("Entry", final), "stop": _number("Stop", final),
        "reports": {"market": said["market"], "sentiment": said["sentiment"], "news": said["news_report"],
                    "fundamentals": said["fundamentals_report"],
                    "debate": f"Bull: {said['bull']}\n\nBear: {said['bear']}",
                    "research_plan": said["research_plan"], "trader": said["trader"],
                    "risk_debate": (f"Aggressive: {said['aggressive']}\n\nConservative: "
                                    f"{said['conservative']}\n\nNeutral: {said['neutral']}"),
                    "final": final},
        "gaps": gaps,
    }
    return decision, usage


def run(tickers, path: Path = LOG, *, complete: Complete | None = None, router=None,
        now: pd.Timestamp | None = None, max_usd: float = MAX_USD, log=print) -> int:
    """Today's call for each ticker through the API. Returns how many did not get one."""
    from . import regime, technicals
    from .data import Router

    now = (now or pd.Timestamp.now(tz=IST)).tz_convert(IST)
    today = f"{now:%Y-%m-%d}"
    tickers = [t.upper().removesuffix(".NS") for t in tickers]
    if complete is None:
        key = api_key()
        if not key:
            raise SystemExit(f"{KEY_ENV} is not set. Put `{KEY_ENV}=...` in a .env file in this folder "
                             "(git-ignored); don't export it in the shell, or Claude Code bills that key too.")
        complete = anthropic_complete(key)
    router = router or Router()
    if now.hour < 16:
        log(f"note: it is {now:%H:%M} IST; the test's calls are made after 16:00, once today's bar is in")
    mood = regime.from_router(router, now).text()

    def closes(t):
        return technicals.bars_known_at(router.fetch("prices", t, now)[1], now)["close"]

    failed, spent = 0, 0.0
    for t in tickers:
        decisions = load(path)
        if any(d["ticker"] == t and d["as_of"][:10] == today for d in decisions):
            log(f"{t}: already has a call for {today}, skipped")
            continue
        if spent >= max_usd:
            log(f"{t}: not run, this run has spent ${spent:.2f} (limit ${max_usd:.2f}); run again to "
                "carry on, with a higher --max-usd if that spend is expected")
            failed += 1
            continue
        usage = {"usd": 0.0}
        try:
            pack = data_pack(router, t, now, mood, past_calls(t, decisions, closes))
            if pack["technicals"].lstrip().startswith(GAP_WORDS):
                # Nothing to decide from, and a recorded call can't be redone: spend nothing, call later.
                log(f"{t}: no call, there are no prices to decide from; run swingbot-refresh, then "
                    f"this again ({pack['technicals'][:160]})")
                failed += 1
                continue
            decision, _ = decide(t, now, pack, complete, usage)
        except Exception as e:   # one bad stock must not stop the list; with no decision, a rerun is not a second call
            spent += usage["usd"]   # the roles that answered before it failed were still paid for
            log(f"{t}: no call ({type(e).__name__}: {str(e)[:300]})")
            failed += 1
            continue
        spent += usage["usd"]
        try:
            decision["atr14"] = technicals.indicators(
                technicals.bars_known_at(router.fetch("prices", t, now)[1], now))["atr_14"]
        except Exception:
            decision["atr14"] = None
        decision.update(engine="api", model=MODEL, **{**usage, "usd": round(usage["usd"], 4)})
        out = path.parent / today / f"{t}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(decision, indent=1))
        try:
            record(decision, path, now)
        except ValueError as e:   # the decision file stays, so what was paid for can still be read
            log(f"{t}: decided {decision['rating']} but not recorded: {e} (kept in {out})")
            failed += 1
            continue
        log(f"{t}: {decision['rating']}  ({usage['llm_calls']} calls, {usage['tokens_in']:,} in / "
            f"{usage['tokens_out']:,} out, ${usage['usd']:.3f})")
    return failed


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2%}"


def _line(x: dict) -> str:
    line = f"{x['as_of'][:10]}  {x['ticker']:<11} {x['rating']:<11} "
    if x["status"] != "settled":
        return line + x["status"]
    return line + (f"{x['entry_day']}..{x['exit_day']}  raw {_pct(x['raw'])}  vs Nifty {_pct(x['alpha'])}"
                   f"  in the call's direction {_pct(x['signed_alpha'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-agent-calls", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=LOG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="check one decision JSON and append it to the log")
    r.add_argument("decision", type=Path)
    s = sub.add_parser("score", help="settle every call whose 5 sessions have traded")
    s.add_argument("--ticker", help="only this stock's calls")
    u = sub.add_parser("run", help="make today's calls through the API (needs ANTHROPIC_API_KEY in .env)")
    u.add_argument("tickers", nargs="*", help="NSE symbols; none means the test's watchlist, in its order")
    u.add_argument("--max-usd", type=float, default=MAX_USD,
                   help=f"stop before the next stock once the run has spent this much (default {MAX_USD})")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        if args.tickers:
            print("note: these are logged beside the test's calls, outside the watchlist schedule")
        return 1 if run(args.tickers or WATCHLIST, args.log, max_usd=args.max_usd) else 0

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
        print(_line(x))
    hit = f"{sm['hit_rate']:.0%}" if sm["directional"] else "n/a"
    t = f"{sm['t']:.2f}" if not math.isnan(sm["t"]) else "n/a"
    print(f"{sm['calls']} calls: {sm['settled']} settled, {sm['pending']} pending, {sm['review']} unreadable "
          f"(REVIEW). Directional and settled: {sm['directional']}; hit rate {hit}; mean alpha in the "
          f"call's direction {_pct(sm['mean_signed_alpha'])}, t {t}")
    engines = [d.get("engine", "skill") for d in decisions]
    if "api" in engines:
        print("Engines: " + ", ".join(f"{e} {engines.count(e)}" for e in sorted(set(engines))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
