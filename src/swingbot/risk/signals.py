"""From swarm calls to paper order tickets: the step between the swarm and the risk engine.

Each call is the ReportAgent's JSON (ticker, sentiment, confidence,
time_horizon, and optionally the time it was made). It goes through, in order:

1. the call itself: bullish only (the bot is long-only CNC; a bearish call is
   not a short), a swing horizon, fresh, and confident enough (a separate,
   higher bar can be set for a defensive market mood).
2. one call per stock (the most confident), best calls first, at most
   `max_new_orders` tickets per run.
3. a technical overlay from bars known at decision time: above the 200-day
   SMA, not overbought (RSI), not stretched far above EMA10.
4. the exit plan, the size (skipped when it comes out under
   `min_order_value`) and the PASS/BLOCK gate, exactly as
   `swingbot-risk plan`, `size` and `check` do them. Every verdict goes in the
   gate's log, and a weekly breach latches the kill switch.
5. a paper ticket for each PASS, with a time exit from the horizon.

Orders in one run are sized one after another against the same account: each
ticket's cash and open risk count against the next call, so three calls in a
morning cannot spend the account three times.

The filters in config/signals.toml can only skip a call. They never loosen a
limit in config/risk.toml, and nothing here sends an order anywhere.
"""

from __future__ import annotations

import csv
import json
import math
import tomllib
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from ..data.router import Router
from ..models import IST, Sentiment, Signal
from ..technicals import bars_known_at, indicators
from .calendar import NSECalendar
from .config import RiskConfig
from .exits import PlanRefused, plan_exit
from .gate import KillSwitch, check_order
from .portfolio import Order, Portfolio, Position, load_sectors
from .sizing import SizingRefused, size_position
from .ticket import TicketRefused, make_ticket

DEFAULT_RULES = Path("config/signals.toml")
MARKET_OPEN = time(9, 15)


@dataclass(frozen=True)
class SignalRules:
    # Defaults match config/signals.toml; docs/research/signal-thresholds.md has the evidence.
    min_confidence: float = 0.60
    defensive_min_confidence: float = 0.60
    skip_when_mood_unknown: bool = False
    min_horizon_hours: int = 72
    max_horizon_hours: int = 480
    max_signal_age_sessions: int = 1
    require_above_sma200: bool = True
    require_above_sma50: bool = False
    max_rsi: float = 80.0
    max_extension_atr: float = 3.0
    stop_rule: str = "atr:2.5"
    target_rule: str = "2R"
    max_new_orders: int = 3
    min_order_value: float = 25_000.0

    def __post_init__(self) -> None:
        if not 0 <= self.min_confidence <= self.defensive_min_confidence <= 1:
            raise ValueError("need 0 <= min_confidence <= defensive_min_confidence <= 1: "
                             "a defensive market can only raise the bar")
        if not 0 < self.min_horizon_hours <= self.max_horizon_hours:
            raise ValueError("need 0 < min_horizon_hours <= max_horizon_hours")
        if self.max_new_orders < 0 or self.max_signal_age_sessions < 1 or self.min_order_value < 0:
            raise ValueError("max_new_orders and min_order_value must be >= 0, max_signal_age_sessions >= 1")

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SignalRules":
        """Read the TOML; with no path, the repo default if present, else built-in defaults."""
        path = Path(path) if path else DEFAULT_RULES
        if not path.exists():
            if path != DEFAULT_RULES:
                raise FileNotFoundError(f"no signal rules at {path}")
            return cls()
        flat: dict = {}
        for section in tomllib.loads(path.read_text()).values():
            flat.update(section)
        unknown = sorted(set(flat) - {f.name for f in fields(cls)})
        if unknown:
            raise ValueError(f"unknown signal rules in {path}: {', '.join(unknown)}")
        return cls(**flat)


@dataclass(frozen=True)
class Call:
    """One swarm call as read from the file. `signal` is None when the row could not be read."""

    ref: str                   # event_id if given, else the row number
    signal: Signal | None
    made_at: pd.Timestamp | None = None
    error: str = ""


@dataclass
class Decision:
    ref: str
    ticker: str
    confidence: float | None
    status: str = "skipped"    # skipped, refused, blocked or ticket
    stage: str = ""            # where it stopped: call, batch, technicals, plan, size, gate, ticket
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    technicals: dict = field(default_factory=dict)
    order: dict | None = None
    verdict: dict | None = None
    ticket: dict | None = None

    def stop(self, status: str, stage: str, *reasons: str) -> "Decision":
        self.status, self.stage = status, stage
        self.reasons += list(reasons)
        return self

    def to_json(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        conf = "" if self.confidence is None else f" ({self.confidence:.2f})"
        head = f"{self.status.upper():8} {self.ticker or '?'}{conf} [{self.ref}]"
        if self.ticket:
            e, x, h = self.ticket["entry"], self.ticket["exits"], self.ticket["hold"]
            head += (f"  buy {e['quantity']} limit {e['price']:,.2f} = {self.ticket['amount']:,.0f}; "
                     f"stop {x['stop']['triggerPrice']:,.2f}, target {x['target']['triggerPrice']:,.2f}; "
                     f"sell by the {h['exit_by']} close")
        lines = [head] + [f"  {self.stage}: {r}" for r in self.reasons] + [f"  note  {n}" for n in self.notes]
        return "\n".join(lines)


# --- reading calls -----------------------------------------------------------------

def _timestamp(value) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    ts = pd.Timestamp(value)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def _call(ref: str, row: dict) -> Call:
    ref = str(row.get("event_id") or ref)
    try:
        signal = Signal(
            ticker=str(row["ticker"]).strip().upper(),
            sentiment=Sentiment(str(row["sentiment"]).strip().lower()),
            confidence=float(row["confidence"]),
            time_horizon=str(row.get("time_horizon") or "72h"),
            score=float(row.get("score") or 0.0),
            rationale=str(row.get("rationale") or ""),
        )
        signal.horizon_hours  # noqa: B018  (raises on a horizon it cannot read)
        made_at = _timestamp(row.get("as_of") or row.get("published_at"))
    except (KeyError, ValueError, TypeError) as e:
        return Call(ref, None, error=f"cannot read this call ({type(e).__name__}: {e})")
    return Call(ref, signal, made_at)


def read_calls(path: str | Path) -> list[Call]:
    """Calls from JSONL (one object per line), a JSON list, or CSV with the same column names."""
    path = Path(path)
    text = path.read_text()
    if path.suffix.lower() == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
        return [_call(f"row {i}", r) for i, r in enumerate(rows, start=2)]
    if text.lstrip().startswith("["):
        return [_call(f"item {i}", r) for i, r in enumerate(json.loads(text), start=1)]
    calls = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            calls.append(_call(f"line {i}", json.loads(line)))
        except json.JSONDecodeError as e:
            calls.append(Call(f"line {i}", None, error=f"not JSON ({e.msg})"))
    return calls


def load_results_dates(path: str | Path | None) -> dict[str, date]:
    """`symbol,date` CSV of each company's next results date (from its board meeting notice)."""
    if not path:
        return {}
    with Path(path).open(newline="") as fh:
        return {r["symbol"].strip().upper(): date.fromisoformat(r["date"].strip()) for r in csv.DictReader(fh)}


# --- the steps -----------------------------------------------------------------------

def fresh_since(as_of: pd.Timestamp, sessions: int, calendar: NSECalendar) -> pd.Timestamp:
    """The oldest call time still traded: the open of the session `sessions` before the entry session.

    With 1, a call made after 09:15 on the last session before the entry is
    fresh; weekend and holiday news still counts for Monday.
    """
    day = entry_session(as_of, calendar)
    for _ in range(sessions):
        day -= timedelta(days=1)
        while not calendar.is_session(day):
            day -= timedelta(days=1)
    return pd.Timestamp(datetime.combine(day, MARKET_OPEN), tz=IST)


def screen_call(call: Call, as_of: pd.Timestamp, rules: SignalRules, mood: dict | None,
                calendar: NSECalendar | None = None) -> Decision:
    """Step 1, on the call alone. Returns a decision still in progress (stage "") if it survives."""
    s = call.signal
    d = Decision(call.ref, s.ticker if s else "", s.confidence if s else None)
    if s is None:
        return d.stop("skipped", "call", call.error)
    if s.sentiment is Sentiment.BEARISH:
        return d.stop("skipped", "call", "bearish: the bot is long-only CNC, so a bearish call is a pass, never a short")
    if s.sentiment is Sentiment.NEUTRAL:
        return d.stop("skipped", "call", "neutral: no trade")
    h = s.horizon_hours
    if not rules.min_horizon_hours <= h <= rules.max_horizon_hours:
        return d.stop("skipped", "call", f"horizon {s.time_horizon} is outside the swing window "
                      f"({rules.min_horizon_hours}h to {rules.max_horizon_hours}h)")
    if call.made_at is None:
        d.notes.append("the call has no as_of time; treated as fresh")
    elif call.made_at > as_of:
        return d.stop("skipped", "call", f"the call is stamped {call.made_at:%Y-%m-%d %H:%M}, after the "
                      f"decision time {as_of:%Y-%m-%d %H:%M}; that is the future")
    else:
        since = fresh_since(as_of, rules.max_signal_age_sessions, calendar or NSECalendar())
        if call.made_at < since:
            return d.stop("skipped", "call", f"the call is from {call.made_at:%Y-%m-%d %H:%M}, before "
                          f"{since:%Y-%m-%d %H:%M} (news older than {rules.max_signal_age_sessions} session(s) "
                          "before the entry is already in the price)")

    verdict = (mood or {}).get("verdict")
    bar = rules.min_confidence
    if verdict == "defensive":
        bar = rules.defensive_min_confidence
    elif verdict in (None, "unknown") and rules.skip_when_mood_unknown:
        why = "not checked" if mood is None else "unknown (too few series)"
        return d.stop("skipped", "call", f"the market mood is {why} and skip_when_mood_unknown is on")
    if s.confidence < bar:
        mood_word = " in a defensive market" if verdict == "defensive" else ""
        return d.stop("skipped", "call", f"confidence {s.confidence:.2f} is under {bar:.2f}{mood_word}")
    return d


def technical_overlay(bars: pd.DataFrame, rules: SignalRules) -> tuple[list[str], dict]:
    """Step 3. Returns (reasons to skip, the numbers used). `bars` must be cut to what is known."""
    if len(bars) < 20:
        return [f"only {len(bars)} sessions of price history"], {}
    v = indicators(bars)
    close, sma200, sma50, rsi, ema10, atr = (v[k] for k in ("close", "sma_200", "sma_50", "rsi_14", "ema_10", "atr_14"))
    facts = {"as_of": f"{bars.index[-1]:%Y-%m-%d}",
             **{k: None if x is None else round(x, 2) for k, x in
                (("close", close), ("sma_200", sma200), ("sma_50", sma50), ("rsi_14", rsi),
                 ("ema_10", ema10), ("atr_14", atr))}}
    why = []
    if rules.require_above_sma200:
        if sma200 is None:
            why.append(f"needs 200 sessions for the 200-day SMA, has {len(bars)}")
        elif close < sma200:
            why.append(f"close {close:,.2f} is under the 200-day SMA {sma200:,.2f}: no longs in a downtrend")
    if rules.require_above_sma50:
        if sma50 is None:
            why.append(f"needs 50 sessions for the 50-day SMA, has {len(bars)}")
        elif close < sma50:
            why.append(f"close {close:,.2f} is under the 50-day SMA {sma50:,.2f}")
    if rsi is not None and rsi > rules.max_rsi:
        why.append(f"RSI14 {rsi:.1f} is over {rules.max_rsi:g}: overbought, the news may already be in the price")
    if ema10 is not None and atr:
        stretch = (close - ema10) / atr
        facts["atr_above_ema10"] = round(stretch, 2)
        if stretch > rules.max_extension_atr:
            why.append(f"close is {stretch:.1f} ATRs above EMA10 (limit {rules.max_extension_atr:g}): chasing")
    return why, facts


def entry_session(as_of: pd.Timestamp, calendar: NSECalendar) -> date:
    """The session a DAY order placed at `as_of` would trade in."""
    local = as_of.tz_convert(IST)
    day = local.date()
    if calendar.is_session(day) and local.time() < MARKET_OPEN:
        return day
    return calendar.next_session(day + timedelta(days=1))


def hold_plan(signal: Signal, as_of: pd.Timestamp, calendar: NSECalendar) -> dict:
    """The time exit: the backtest's rule, N = ceil(horizon / 24h) sessions counting the entry day."""
    n = max(1, math.ceil(signal.horizon_hours / 24))
    start = entry_session(as_of, calendar)
    return {"max_sessions": n, "entry_session": start.isoformat(),
            "exit_by": calendar.add_sessions(start, n - 1).isoformat(),
            "rule": "if neither the stop nor the target has filled, sell at the close of exit_by"}


def decide(calls: list[Call], *, as_of: pd.Timestamp, portfolio: Portfolio,
           fetch_bars: Callable[[str], pd.DataFrame], config: RiskConfig | None = None,
           rules: SignalRules | None = None, mood: dict | None = None,
           results_dates: dict[str, date] | None = None, sectors: dict[str, str] | None = None,
           calendar: NSECalendar | None = None, kill_switch: KillSwitch | None = None,
           verdict_log: Path | None = None, gate: bool = True) -> list[Decision]:
    """Run every call through the steps. Returns one decision per call, in the order they were ranked.

    `fetch_bars(ticker)` returns daily bars; they are cut to `as_of` here.
    With `verdict_log` set, each verdict is appended to it and a weekly breach
    latches `kill_switch`; without it nothing is written.

    With `gate=False` it stops after sizing: each surviving call ends as
    status "sized" with its order, for a caller that runs the gate and the
    ticket itself (the daily loop). Nothing is checked, logged or latched.
    """
    config, rules = config or RiskConfig(), rules or SignalRules()
    calendar = calendar or NSECalendar()
    results_dates, sectors = results_dates or {}, sectors or {}
    as_of = as_of.tz_convert(IST)

    done: list[Decision] = []
    live: list[tuple[Call, Decision]] = []
    for call in calls:
        d = screen_call(call, as_of, rules, mood, calendar)
        if d.stage:
            done.append(d)
        else:
            live.append((call, d))

    # One call per stock, the most confident; then best calls first.
    live.sort(key=lambda cd: (-cd[0].signal.confidence, -abs(cd[0].signal.score), cd[0].ref))
    seen: set[str] = set()
    ranked = []
    for call, d in live:
        if call.signal.ticker in seen:
            done.append(d.stop("skipped", "batch", f"a more confident call for {call.signal.ticker} is in this batch"))
        else:
            seen.add(call.signal.ticker)
            ranked.append((call, d))

    book, tickets = portfolio, 0
    for call, d in ranked:
        done.append(d)
        s = call.signal
        if tickets >= rules.max_new_orders:
            d.stop("skipped", "batch", f"already {tickets} orders this run (max_new_orders {rules.max_new_orders})")
            continue
        try:
            bars = bars_known_at(fetch_bars(s.ticker), as_of)
        except Exception as e:  # noqa: BLE001  (any data failure skips the call, never guesses)
            d.stop("skipped", "technicals", f"no price history ({e})")
            continue
        why, d.technicals = technical_overlay(bars, rules)
        if why:
            d.stop("skipped", "technicals", *why)
            continue
        try:
            plan = plan_exit(s.ticker, bars, None, rules.stop_rule, rules.target_rule, config)
        except PlanRefused as e:
            d.stop("refused", "plan", str(e))
            continue
        try:
            sizing = size_position(plan, book, config, sectors.get(s.ticker, ""), mood=mood)
        except SizingRefused as e:
            d.stop("refused", "size", str(e))
            continue
        order = sizing.order
        d.notes.append(f"size decided by {sizing.binding}; loss at the stop {sizing.loss_at_stop:,.0f} "
                       f"({sizing.loss_with_gap:,.0f} with the gap allowance)")
        if order.qty * order.entry < rules.min_order_value:
            d.stop("skipped", "size", f"{order.qty} shares = {order.qty * order.entry:,.0f}, under the "
                   f"{rules.min_order_value:,.0f} minimum (decided by {sizing.binding}); "
                   "fixed costs would eat a trade this small")
            continue
        order = replace(order, plan={**order.plan, "hold": hold_plan(s, as_of, calendar), "call": call.ref})
        d.order = order.to_json()

        if not gate:
            d.status, d.stage = "sized", "size"
        else:
            latched = kill_switch.latched if kill_switch else False
            verdict = check_order(order, book, config, bars=bars, today=as_of.date(),
                                  results_date=results_dates.get(s.ticker), calendar=calendar,
                                  kill_switch_latched=latched, mood=mood)
            record = {"at": f"{as_of:%Y-%m-%d %H:%M}", **verdict.to_json(), "call": call.ref}
            d.verdict = record
            d.notes += verdict.warnings
            if verdict_log is not None:
                verdict_log.parent.mkdir(parents=True, exist_ok=True)
                with verdict_log.open("a") as fh:
                    fh.write(json.dumps(record) + "\n")
                if verdict.trip_kill_switch and kill_switch:
                    kill_switch.latch(verdict.trip_kill_switch)
            if not verdict.passed:
                d.stop("blocked", "gate", *(f"{c.rule}: {c.detail}" for c in verdict.checks if not c.ok))
                continue

            try:
                ticket = make_ticket(order, record, as_of.date(), config,
                                     kill_switch_latched=kill_switch.latched if kill_switch else False)
            except TicketRefused as e:
                d.stop("refused", "ticket", str(e))
                continue
            ticket["hold"] = hold_plan(s, as_of, calendar)
            ticket["call"] = {"ref": call.ref, **s.to_json(), "rationale": s.rationale}
            d.ticket = ticket
            d.status, d.stage = "ticket", "ticket"
        tickets += 1
        # The next call is sized against the account as if this entry had filled.
        book = replace(book, cash=book.cash - order.qty * order.entry,
                       positions=book.positions + (Position(order.ticker, order.qty, order.entry, order.stop,
                                                            order.sector),))
    return done


def summary(decisions: list[Decision]) -> str:
    counts = {k: sum(d.status == k for d in decisions) for k in ("ticket", "sized", "blocked", "refused", "skipped")}
    head = (f"{len(decisions)} calls: {counts['ticket']} tickets, {counts['blocked']} blocked by the gate, "
            f"{counts['refused']} refused, {counts['skipped']} skipped. Paper only: nothing was sent.")
    if counts["sized"]:
        head = head.replace(" tickets,", f" tickets, {counts['sized']} sized for the gate,", 1)
    order = {"ticket": 0, "sized": 0, "blocked": 1, "refused": 2, "skipped": 3}
    return "\n".join([head] + [d.text() for d in sorted(decisions, key=lambda d: order[d.status])])


def daily_hook(day, signals: list[dict], portfolio: Portfolio, mood: dict | None) -> list[Order]:
    """The daily loop's `signal_to_order` hook (config/daily.toml: "swingbot.risk.signals:daily_hook").

    Runs the filters, the overlay, the plan and the size, but not the gate:
    the loop checks and tickets every order itself. Writes every call's
    decision to `signal_decisions.json` in the day's folder.
    """
    router = Router(day.data)
    calls = [_call(f"signal {i}", {**sig, "as_of": sig.get("as_of") or sig.get("published_at")})
             for i, sig in enumerate(signals, start=1)]
    decisions = decide(calls, as_of=day.as_of, portfolio=portfolio,
                       fetch_bars=lambda ticker: router.fetch("prices", ticker, day.as_of)[1],
                       config=day.risk, rules=SignalRules.load(), mood=mood, sectors=load_sectors(),
                       calendar=day.calendar, gate=False)
    out = day.path("signal_decisions.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"as_of": day.stamp, "decisions": [d.to_json() for d in decisions]}, indent=2) + "\n")
    return [Order.from_json(d.order) for d in decisions if d.status == "sized"]
