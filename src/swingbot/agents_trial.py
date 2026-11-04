"""TradingAgents forward paper test (docs/research/tradingagents-paper-test.md).

Runs TauricResearch/TradingAgents on 20 fixed mid-cap stocks once a week and
scores its ratings on paper. Nothing here places an order.

Two ways to make the weekly decisions:

* Skill (no API key, Harsha's default): Claude Code plays TradingAgents' roles
  using the `tradingagents-weekly` skill (.claude/skills/tradingagents-weekly).

      swingbot-agents brief [--date YYYY-MM-DD] [--data data/raw/eod2_data/daily]
      swingbot-agents pending [--date ...]
      swingbot-agents record --date D --symbol S --rating Buy --summary "..."

  `brief` fetches each stock's data with TradingAgents' own data tools (no
  model calls) into runs/agents-trial/briefs/<date>/. Claude reads a brief,
  writes the analyst, debate, trader, risk and portfolio-manager notes, and
  `record` logs the rating.

* API (needs ANTHROPIC_API_KEY): the full TradingAgents graph on Haiku.

      swingbot-agents run [--date YYYY-MM-DD] [--limit N]

Either way:

    swingbot-agents score --data data/raw/eod2_data/daily     # once eod2_data has the week's closes
    swingbot-agents cost                                       # tokens and dollars so far (API runs)

`run` asks TradingAgents for one decision per stock for the given Friday
(default: the latest Friday on or before today) and appends it to the decision
log. A stock already decided for that date is skipped, so an interrupted run
continues where it stopped. Every LLM call's tokens are counted and priced.

`score` holds each Buy or Overweight call from the first close after the
decision date to the fifth close after that, the rule TradingAgents' own
backtest uses. The paper book puts equal money in every such call each week
and pays 0.20% a side; the comparison is the same 20 stocks held in equal
weight, and the Nifty Midcap 150.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .momentum import adjust_jumps

STATE = Path("data/state/agents_trial")
DECISIONS = STATE / "decisions.jsonl"
RUNS = Path("runs/agents-trial")
REPORT = RUNS / "report.md"

# Fixed before the first run: random.Random(20261102).sample(sorted midcap150 symbols), 20.
STOCKS = ["ASTERDM", "AUBANK", "BAJAJHFL", "CRISIL", "FEDERALBNK", "GODFRYPHLP", "GODREJPROP",
          "GVT&D", "LICHSGFIN", "MFSL", "NH", "NHPC", "NMDC", "PATANJALI", "PIRAMALFIN", "SRF",
          "SWIGGY", "UNOMINDA", "WAAREEENER", "YESBANK"]
START = "2026-11-06"  # first Friday decided
MODEL = "claude-haiku-5-5"
# USD per million tokens: (input, output) up to the cut, (input, output) above it, cut in prompt tokens.
PRICE = {"claude-haiku-5-5": ((0.10, 0.50), (0.50, 2.50), 100_000)}
ANALYSTS = ("market", "news", "fundamentals")  # social reads StockTwits/Reddit, which barely cover NSE
LONG = {"Buy", "Overweight"}
HOLD_SESSIONS = 5
COST_SIDE = 0.002
INDEX = "nifty midcap 150"


def yahoo(symbol: str) -> str:
    return f"{symbol}.NS"


def last_friday(today: date) -> date:
    return today - timedelta(days=(today.weekday() - 4) % 7)


def call_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    (lo_in, lo_out), (hi_in, hi_out), cut = PRICE[model]
    pin, pout = (hi_in, hi_out) if tokens_in > cut else (lo_in, lo_out)
    return (tokens_in * pin + tokens_out * pout) / 1e6


def read_decisions(path: Path = DECISIONS) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- run


def _meter(model: str):
    """A LangChain callback that counts calls and prices each one on its own prompt size."""
    from langchain_core.callbacks import BaseCallbackHandler

    class Meter(BaseCallbackHandler):
        def __init__(self):
            self.lock = threading.Lock()
            self.calls = self.tokens_in = self.tokens_out = 0
            self.usd = 0.0

        def on_llm_end(self, response, **kwargs):
            usage = {}
            try:
                usage = response.generations[0][0].message.usage_metadata or {}
            except (AttributeError, IndexError, TypeError):
                pass
            t_in, t_out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            with self.lock:
                self.calls += 1
                self.tokens_in += t_in
                self.tokens_out += t_out
                self.usd += call_cost(model, t_in, t_out)

    return Meter()


def make_config(model: str = MODEL) -> dict:
    from tradingagents.default_config import build_default_config

    cfg = build_default_config()
    STATE.mkdir(parents=True, exist_ok=True)
    cfg.update({
        "llm_provider": "anthropic",
        "quick_think_llm": model,
        "deep_think_llm": model,
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "results_dir": str(RUNS / "logs"),
        # The trial keeps its own decision memory, so lessons come only from its own past calls.
        "memory_log_path": str(STATE / "trading_memory.md"),
        # SEC EDGAR only covers US filers; Indian fundamentals come from Yahoo.
        "data_vendors": {**cfg["data_vendors"], "fundamental_data": "yfinance"},
        "global_news_queries": [
            "India stock market Nifty Sensex",
            "Reserve Bank of India interest rates inflation",
            "India economy GDP earnings outlook",
            "foreign investors FPI flows India",
            "oil prices rupee",
        ],
    })
    return cfg


def run(day: date, stocks: list[str], model: str = MODEL, path: Path = DECISIONS, log=print) -> int:
    from tradingagents import __version__ as ta_version
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    done = {(d["date"], d["symbol"]) for d in read_decisions(path) if d.get("rating")}
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = make_config(model)
    errors = 0
    for sym in stocks:
        if (day.isoformat(), sym) in done:
            log(f"{sym}: already decided for {day}, skipped")
            continue
        meter = _meter(model)
        graph = TradingAgentsGraph(selected_analysts=ANALYSTS, config=cfg, callbacks=[meter])
        t0 = time.time()
        row = {"date": day.isoformat(), "symbol": sym, "ticker": yahoo(sym), "model": model,
               "tradingagents": ta_version}
        try:
            _, rating = graph.propagate(yahoo(sym), day.isoformat())
            row["rating"] = str(rating)
        except Exception as exc:  # one bad stock must not stop the week
            row["rating"] = None
            row["error"] = f"{type(exc).__name__}: {exc}"[:500]
            errors += 1
        row.update({"llm_calls": meter.calls, "tokens_in": meter.tokens_in, "tokens_out": meter.tokens_out,
                    "usd": round(meter.usd, 4), "seconds": round(time.time() - t0, 1)})
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        log(f"{sym}: {row['rating'] or row['error']}  ({meter.calls} calls, "
            f"{meter.tokens_in:,} in / {meter.tokens_out:,} out, ${meter.usd:.3f}, {row['seconds']}s)")
    return errors


# ---------------------------------------------------------------- skill: brief / record

RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
BRIEFS = RUNS / "briefs"
NEWS_DAYS = 7


def _safe(fn, *args) -> str:
    try:
        out = fn(*args)
    except Exception as exc:  # a missing source is reported in the brief, never fatal
        return f"(unavailable: {type(exc).__name__}: {str(exc)[:200]})"
    return str(out).strip() or "(no data)"


def past_calls(symbol: str, day: date, decisions: list[dict], data: Path | None) -> str:
    """This stock's earlier ratings and, when prices are at hand, how each turned out.

    TradingAgents' memory log feeds past decisions and outcomes back to the
    Portfolio Manager; this is the same idea from the trial's own log."""
    rows = sorted((d for d in decisions if d.get("symbol") == symbol and d.get("rating")
                   and d["date"] < day.isoformat()), key=lambda d: d["date"])[-6:]
    if not rows:
        return "No earlier calls on this stock."
    s = closes(data, symbol) if data else None
    out = []
    for d in rows:
        w = window(s, date.fromisoformat(d["date"]))
        res = f"5-day return {w[2]:+.1%}" if w else "outcome not in the data yet"
        out.append(f"- {d['date']}: {d['rating']} ({res})")
    return "\n".join(out)


def brief(day: date, stocks: list[str], data: Path | None = None, out_dir: Path | None = None,
          log=print) -> Path:
    """Write one data pack per stock, as TradingAgents' analysts would fetch it, cut at `day`."""
    from tradingagents.dataflows.config import set_config
    from tradingagents.dataflows.router import route_to_vendor
    from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot

    gap = too_soon(day, read_decisions())
    if gap:
        raise SystemExit(gap)
    set_config(make_config())
    out = (out_dir or BRIEFS) / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    d, start = day.isoformat(), (day - timedelta(days=NEWS_DAYS)).isoformat()
    (out / "_market.md").write_text(
        f"# India market news, week to {d}\n\n" + _safe(route_to_vendor, "get_global_news", d, NEWS_DAYS, 10) + "\n")
    decisions = read_decisions()
    for sym in stocks:
        t = yahoo(sym)
        parts = [
            f"# {sym} ({t}), decision date {d}",
            "Data is cut at the decision date. Prices are in rupees.",
            "## Market data (verified snapshot: latest bar, indicators, last 30 sessions)",
            _safe(build_verified_market_snapshot, t, d, 30),
            "## Fundamentals",
            _safe(route_to_vendor, "get_fundamentals", t, d),
            f"## Company news, {start} to {d}",
            _safe(route_to_vendor, "get_news", t, start, d),
            "## This stock's earlier calls in the trial",
            past_calls(sym, day, decisions, data),
        ]
        (out / f"{sym}.md").write_text("\n\n".join(parts) + "\n")
        log(f"{sym}: brief written")
    return out


def latest_decision_date(decisions: list[dict]) -> date | None:
    days = [d["date"] for d in decisions if d.get("rating")]
    return date.fromisoformat(max(days)) if days else None


def too_soon(day: date, decisions: list[dict], today: date | None = None) -> str | None:
    """Hold windows must not overlap: a new decision date needs 7+ days after the last one."""
    last = latest_decision_date(decisions)
    if last and last != day and (day - last).days < 7:
        return (f"last decisions were for {last}; the next can be made from {last + timedelta(days=7)} "
                "so the 5-session hold windows don't overlap")
    if day > (today or date.today()):
        return f"{day} is in the future"
    return None


def pending(day: date, stocks: list[str], decisions: list[dict]) -> list[str]:
    done = {d["symbol"] for d in decisions if d["date"] == day.isoformat() and d.get("rating")}
    return [s for s in stocks if s not in done]


RECORD_GRACE_DAYS = 3  # a Friday's calls may be recorded over the weekend, not later


def record(day: date, symbol: str, rating: str, summary: str = "", engine: str = "skill",
           model: str = "", path: Path = DECISIONS, today: date | None = None) -> dict:
    today = today or date.today()
    if day > today:
        raise ValueError(f"{day} is in the future")
    if (today - day).days > RECORD_GRACE_DAYS:
        raise ValueError(f"{day} is more than {RECORD_GRACE_DAYS} days ago; a model may know what "
                         "happened since, so late calls are not logged")
    rating = rating.strip().capitalize()
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {', '.join(RATINGS)}, got {rating!r}")
    if symbol not in STOCKS:
        raise ValueError(f"{symbol} is not one of the trial's 20 stocks")
    if any(d["date"] == day.isoformat() and d["symbol"] == symbol and d.get("rating")
           for d in read_decisions(path)):
        raise ValueError(f"{symbol} already has a decision for {day}")
    row = {"date": day.isoformat(), "symbol": symbol, "ticker": yahoo(symbol), "rating": rating,
           "engine": engine, "model": model, "summary": summary[:1000],
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


# ---------------------------------------------------------------- score


def closes(data: Path, symbol: str) -> pd.Series | None:
    p = data / f"{symbol.lower()}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    if "Series" in df:
        df = df[df["Series"].fillna("EQ") == "EQ"]
    s = df["Close"].astype(float)
    s = s[~s.index.duplicated()].sort_index()
    return adjust_jumps(s) if symbol.lower() != INDEX else s


def window(s: pd.Series | None, day: date) -> tuple[pd.Timestamp, pd.Timestamp, float] | None:
    """(entry day, exit day, return) from the first close after `day` to HOLD_SESSIONS closes later."""
    if s is None:
        return None
    after = s[s.index > pd.Timestamp(day)]
    if len(after) <= HOLD_SESSIONS:
        return None
    return after.index[0], after.index[HOLD_SESSIONS], float(after.iloc[HOLD_SESSIONS] / after.iloc[0] - 1)


def score(decisions: list[dict], data: Path, stocks: list[str] = STOCKS) -> dict:
    px = {s: closes(data, s) for s in stocks}
    idx = closes(data, INDEX)
    by_date: dict[str, dict[str, str | None]] = {}
    for d in decisions:
        if d.get("symbol") in px:
            by_date.setdefault(d["date"], {})[d["symbol"]] = d.get("rating")
    weeks, calls = [], []
    held: dict[str, float] = {}
    for day_s in sorted(by_date):
        day = date.fromisoformat(day_s)
        rets = {s: w[2] for s in stocks if (w := window(px[s], day))}
        if not rets or len(rets) < len(stocks) // 2:
            continue  # the week's closes are not in the data yet
        ratings = by_date[day_s]
        for s, r in ratings.items():
            if s in rets:
                calls.append({"date": day_s, "symbol": s, "rating": r, "ret": rets[s]})
        ew = sum(rets.values()) / len(rets)
        longs = [s for s, r in ratings.items() if r in LONG and s in rets]
        gross = sum(rets[s] for s in longs) / len(longs) if longs else 0.0
        # Back to equal weight each week; pay COST_SIDE on every rupee bought or sold (drift ignored).
        new = {s: 1 / len(longs) for s in longs}
        traded = sum(abs(new.get(s, 0) - held.get(s, 0)) for s in set(new) | set(held))
        net = gross - COST_SIDE * traded
        held = new
        wi = window(idx, day)
        weeks.append({"date": day_s, "longs": longs, "gross": gross, "net": net, "ew": ew,
                      "index": wi[2] if wi else None, "decided": len(ratings),
                      "review": sum(1 for r in ratings.values() if r not in (*LONG, "Hold", "Underweight", "Sell"))})
    return {"weeks": weeks, "calls": calls}


def _t(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd > 0 else None


def compound(xs) -> float:
    v = 1.0
    for x in xs:
        v *= 1 + x
    return v - 1


def report(result: dict, decisions: list[dict]) -> str:
    weeks, calls = result["weeks"], result["calls"]
    lines = ["# TradingAgents paper test", "",
             f"Rules: docs/research/tradingagents-paper-test.md. 20 fixed mid-caps, weekly from {START}. "
             "Engines: " + (", ".join(sorted({f"{d.get('engine', 'api')} ({d.get('model') or '?'})"
                                             for d in decisions})) or "none yet") + ".", ""]
    usd = sum(d.get("usd", 0) for d in decisions)
    lines += [f"Decisions logged: {len(decisions)} ({sum(1 for d in decisions if not d.get('rating'))} failed). "
              f"Model cost so far: ${usd:.2f}.", ""]
    if not weeks:
        return "\n".join(lines + ["No week has its closes in the data yet."]) + "\n"
    lines += ["| Week of | Long calls | Book (net) | Same 20, equal weight | Nifty Midcap 150 |",
              "|---|---|---|---|---|"]
    for w in weeks:
        ix = f"{w['index']:+.2%}" if w["index"] is not None else "n/a"
        lines.append(f"| {w['date']} | {len(w['longs'])} | {w['net']:+.2%} | {w['ew']:+.2%} | {ix} |")
    book, ew = compound(w["net"] for w in weeks), compound(w["ew"] for w in weeks)
    ixs = [w["index"] for w in weeks if w["index"] is not None]
    lines += ["", f"Since the start ({len(weeks)} weeks): book {book:+.2%}, same 20 equal weight {ew:+.2%}"
              + (f", Nifty Midcap 150 {compound(ixs):+.2%}" if len(ixs) == len(weeks) else "") + ".", ""]
    # Pre-registered test: each week, average 5-day return of long calls minus that week's equal weight.
    excess = []
    for w in weeks:
        if w["longs"]:
            excess.append(w["gross"] - w["ew"])
    t = _t(excess)
    lines.append(f"Long calls vs equal weight, week by week: {len(excess)} weeks with a long call, mean "
                 + (f"{sum(excess) / len(excess):+.2%}" if excess else "n/a")
                 + (f", t {t:.2f}" if t is not None else "") + ".")
    by_rating: dict[str, list[float]] = {}
    week_ew = {w["date"]: w["ew"] for w in weeks}
    for c in calls:
        by_rating.setdefault(str(c["rating"]), []).append(c["ret"] - week_ew[c["date"]])
    lines += ["", "| Rating | Calls | Mean 5-day return vs that week's equal weight | Beat it |", "|---|---|---|---|"]
    for r in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        xs = by_rating.pop(r, [])
        if xs:
            lines.append(f"| {r} | {len(xs)} | {sum(xs) / len(xs):+.2%} | {sum(x > 0 for x in xs) / len(xs):.0%} |")
    for r, xs in by_rating.items():
        lines.append(f"| {r} (unreadable) | {len(xs)} | {sum(xs) / len(xs):+.2%} | {sum(x > 0 for x in xs) / len(xs):.0%} |")
    return "\n".join(lines) + "\n"


def cost_summary(decisions: list[dict]) -> str:
    decisions = [d for d in decisions if d.get("engine", "api") == "api"]
    if not decisions:
        return "No API-run decisions logged (skill runs use the Claude plan and cost nothing extra)."
    by_week: dict[str, list[dict]] = {}
    for d in decisions:
        by_week.setdefault(d["date"], []).append(d)
    out = []
    for wk, ds in sorted(by_week.items()):
        out.append(f"{wk}: {len(ds)} stocks, {sum(d.get('llm_calls', 0) for d in ds)} calls, "
                   f"{sum(d.get('tokens_in', 0) for d in ds):,} in / {sum(d.get('tokens_out', 0) for d in ds):,} out, "
                   f"${sum(d.get('usd', 0) for d in ds):.2f}")
    per = sum(d.get("usd", 0) for d in decisions) / len(decisions)
    out.append(f"Average ${per:.3f} per stock; 20 stocks a week is about ${per * 20 * 52 / 12:.2f} a month.")
    return "\n".join(out)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-agents", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ask TradingAgents for this week's decisions")
    r.add_argument("--date", help="decision date (default: latest Friday on or before today)")
    r.add_argument("--limit", type=int, help="only the first N stocks (for a cost check)")
    r.add_argument("--stocks", help="comma-separated symbols instead of the fixed 20 (not scored)")
    s = sub.add_parser("score", help="score the logged decisions and write the report")
    s.add_argument("--data", type=Path, required=True, help="eod2_data daily folder")
    sub.add_parser("cost", help="tokens and dollars spent so far")
    b = sub.add_parser("brief", help="fetch each stock's data for the skill (no model calls)")
    b.add_argument("--date", help="decision date (default: today; run after the 15:30 close)")
    b.add_argument("--data", type=Path, help="eod2_data daily folder, to show past calls' outcomes")
    b.add_argument("--limit", type=int)
    pe = sub.add_parser("pending", help="stocks still without a decision for the date")
    pe.add_argument("--date")
    rc = sub.add_parser("record", help="log one decision made by the skill")
    rc.add_argument("--date", required=True)
    rc.add_argument("--symbol", required=True)
    rc.add_argument("--rating", required=True, choices=RATINGS)
    rc.add_argument("--summary", default="")
    rc.add_argument("--model", default="", help="the Claude model Claude Code ran on")
    args = ap.parse_args(argv)

    decisions = read_decisions()
    if args.cmd == "run":
        day = date.fromisoformat(args.date) if args.date else last_friday(date.today())
        stocks = args.stocks.split(",") if args.stocks else STOCKS
        if args.limit:
            stocks = stocks[: args.limit]
        errors = run(day, stocks)
        print(cost_summary(read_decisions()))
        return 1 if errors else 0
    if args.cmd in ("brief", "pending"):
        day = date.fromisoformat(args.date) if args.date else (
            date.today() if args.cmd == "brief" else latest_decision_date(decisions) or date.today())
        if args.cmd == "pending":
            print("\n".join(pending(day, STOCKS, decisions)) or "none: all 20 decided")
            return 0
        stocks = STOCKS[: args.limit] if args.limit else STOCKS
        print(f"briefs in {brief(day, stocks, args.data)}")
        return 0
    if args.cmd == "record":
        try:
            row = record(date.fromisoformat(args.date), args.symbol, args.rating, args.summary,
                         model=args.model)
        except ValueError as exc:
            print(f"not recorded: {exc}", file=sys.stderr)
            return 2
        print(f"recorded {row['symbol']} {row['date']}: {row['rating']}")
        return 0
    if args.cmd == "score":
        text = report(score(decisions, args.data), decisions)
        RUNS.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(text)
        print(text)
        return 0
    print(cost_summary(decisions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
