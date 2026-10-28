"""The daily paper trading loop: one run per session day, in two halves.

    start   calendar and hours, data refresh, paper broker settle, the account,
            the weekly kill switch, the market mood, the results dates (R9),
            then the swarm brief for the news since the last session
            opened
    (Claude Code plays the swarm on the brief and writes views.jsonl)
    finish  signals from the views, signal-to-order, the risk gate and a ticket
            for each PASS, paper broker submit, the ledger report, and the
            Verifier brief for trades that closed

The swarm sits in the middle because Claude Code plays it with no API key, so
the loop has to stop and wait for it. Each half writes into
`runs/daily/<date>/`, and `day.json` there carries what `start` decided to
`finish`.

Each half ends with phone alerts (`swingbot.alerts`) for what changed.
Settling, the report and the Verifier always run. New entries only happen on
an NSE session day, before the 15:30 close, with a paper account, and with the
kill switch unlatched. Every order goes through `swingbot-risk check` and
`swingbot-risk ticket` exactly as a hand-made one would, so the verdict log
and the kill switch latch the same way. The loop never resets the kill switch,
never edits a limit and never talks to a real broker.
"""

from __future__ import annotations

import csv
import io
import json
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from .. import alerts, corporate, regime
from ..cli import briefs_main
from ..data.router import DataConfig, Router
from ..events import load_events
from ..memory import ClosedTrade, MemoryStore
from ..memory.cli import main as memory_main
from ..memory.store import MARKET_CLOSE
from ..models import IST
from ..paper.cli import _replay
from ..paper.cli import main as paper_main
from ..paper.ledger import DEFAULT_LEDGER, Ledger
from ..risk.calendar import NSECalendar
from ..risk.cli import main as risk_main
from ..risk.config import RiskConfig
from ..risk.gate import DEFAULT_STATE_DIR, KillSwitch
from ..risk import results
from ..risk.portfolio import Order, Portfolio
from ..risk.ticket import describe as describe_ticket
from ..swarm.reasoners import news_tone
from ..swarm.recorded import MissingViews, RecordedSwarm
from .hooks import BLOCKED, FAILED, HOOKS, OK, SKIPPED, Step, load_hook
from .session import CLOSED, HOLIDAY, OPEN_NOW, PRE_OPEN, ist, news_window, next_session, phase

DEFAULT_CONFIG = Path("config/daily.toml")
DEFAULT_HOOKS = {name: f"swingbot.daily.stubs:{name}" for name in HOOKS}
ENTRIES_LOG = "daily_entries.jsonl"  # fingerprint -> the signal behind it, for the Verifier


@dataclass(frozen=True)
class DailyConfig:
    hooks: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOOKS))
    runs_dir: Path = Path("runs/daily")
    ledger: Path = DEFAULT_LEDGER
    state_dir: Path = DEFAULT_STATE_DIR
    memory_db: Path = Path("runs/memory.db")
    holidays: Path | None = None
    risk_config: str | None = None
    data_config: str | None = None
    batch: int = 10
    with_technicals: bool = True
    with_context: bool = True
    with_mood: bool = True
    with_lessons: bool = True
    alerts: alerts.AlertConfig = field(default_factory=alerts.AlertConfig)
    news_cutoff: str | None = None  # drop news before this date (the walk-forward sets the model's cutoff)
    per_stock: int = 0              # at most this many headlines per stock per day for the swarm (0 = all)
    skip_roundups: bool = False     # leave out headlines naming several stocks ("Stocks to watch: A, B, C")
    min_tone: float = 0.0           # skip headlines whose keyword tone is weaker than this (0 = keep all)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DailyConfig":
        path = Path(path) if path else DEFAULT_CONFIG
        if not path.exists():
            if path != DEFAULT_CONFIG:
                raise FileNotFoundError(f"no daily config at {path}")
            return cls()
        raw = tomllib.loads(path.read_text())
        paths, brief = raw.get("paths", {}), raw.get("brief", {})
        unknown = set(raw.get("hooks", {})) - set(HOOKS)
        if unknown:
            raise ValueError(f"{path}: unknown hooks {sorted(unknown)} (known: {', '.join(HOOKS)})")
        opt = lambda d, k: str(d[k]) if d.get(k) else None  # noqa: E731
        return cls(
            hooks={**DEFAULT_HOOKS, **raw.get("hooks", {})},
            runs_dir=Path(paths.get("runs_dir", "runs/daily")),
            ledger=Path(paths.get("ledger", DEFAULT_LEDGER)),
            state_dir=Path(paths.get("state_dir", DEFAULT_STATE_DIR)),
            memory_db=Path(paths.get("memory_db", "runs/memory.db")),
            holidays=Path(paths["holidays"]) if paths.get("holidays") else None,
            risk_config=opt(paths, "risk_config"),
            data_config=opt(paths, "data_config"),
            batch=int(brief.get("batch", 10)),
            **{k: bool(brief.get(k, True)) for k in ("with_technicals", "with_context", "with_mood", "with_lessons")},
            per_stock=int(brief.get("per_stock", 0)),
            skip_roundups=bool(brief.get("skip_roundups", False)),
            min_tone=float(brief.get("min_tone", 0.0)),
            alerts=alerts.AlertConfig.from_toml(raw.get("alerts", {})),
        )


class Day:
    """One run of the loop: the time, the config, the paths and the hooks."""

    def __init__(self, as_of=None, config: DailyConfig | None = None, hooks: dict | None = None):
        self.as_of = ist(as_of if as_of is not None else datetime.now(IST))
        self.config = config or DailyConfig.load()
        self.risk = RiskConfig.load(self.config.risk_config)
        self.data = DataConfig.load(self.config.data_config)
        self.calendar = NSECalendar.load(self.config.holidays)
        self.ledger = self.config.ledger
        self.state_dir = self.config.state_dir
        self.dir = self.config.runs_dir / f"{self.as_of:%Y-%m-%d}"
        self.hooks = {name: load_hook(spec) for name, spec in self.config.hooks.items()}
        self.hooks.update(hooks or {})
        self.notes: list[str] = []
        self.alert_sender = None  # replaces the configured channel (tests)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    @property
    def stamp(self) -> str:
        return f"{self.as_of:%Y-%m-%d %H:%M}"

    def path(self, name: str) -> Path:
        return self.dir / name

    def common_args(self) -> list[str]:
        args = []
        if self.config.risk_config:
            args += ["--config", self.config.risk_config]
        if self.config.data_config:
            args += ["--data-config", self.config.data_config]
        if self.config.holidays:
            args += ["--holidays", str(self.config.holidays)]
        return args + ["--state-dir", str(self.state_dir)]

    def has_account(self) -> bool:
        try:
            return any(r["kind"] == "open" for r in Ledger(self.ledger).records())
        except ValueError:
            return False


def _run(main, argv: list[str]) -> tuple[int, str]:
    """Run one of the repo's CLIs in-process and keep what it printed."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, buf.getvalue().strip()


def _hook(day: Day, name: str, *args):
    try:
        return day.hooks[name](day, *args)
    except Exception as e:  # a hook's failure is reported, never allowed to skip the report
        return Step(name.replace("_", " "), FAILED, f"{type(e).__name__}: {e}")


# --- start ---------------------------------------------------------------


def start(day: Day) -> dict:
    steps: list[Step] = []
    why_not: list[str] = []
    today = day.as_of.date()
    ph = phase(day.as_of, day.calendar)
    if not day.calendar.knows(today.year):
        # Without the list a holiday looks like a session: entries, fills and the
        # R9 count would all be off, so no new entries until the year is added.
        why_not.append(f"no NSE holiday list for {today.year} in config/nse_holidays.toml; add the year "
                       "from NSE's holiday circular before trading")
    if ph == HOLIDAY:
        why_not.append(f"{today} is not an NSE session (weekend or holiday); the next is "
                       f"{next_session(today, day.calendar)}")
    elif ph == CLOSED:
        why_not.append(f"today's session closed at 15:30; run again before 09:15 on "
                       f"{next_session(today, day.calendar)}")
    elif ph == OPEN_NOW:
        day.note("the market is already open: today's entries miss the opening auction and fill only if the "
                 "price trades under the limit later in the day")
    steps.append(Step("calendar", OK, f"{today:%a %d %b %Y}, {ph.replace('_', ' ')} at {day.as_of:%H:%M} IST"))

    steps.append(_hook(day, "refresh"))
    settle = _hook(day, "broker_settle")
    steps.append(settle)
    if settle.status == FAILED:
        why_not.append("the paper broker could not settle, so the account may be out of date")

    portfolio = _portfolio(day, steps)
    if portfolio is None:
        why_not.append("there is no paper account yet: run `swingbot-paper open --cash N` once")
    if _kill_switch(day, portfolio, steps):
        why_not.append("the weekly kill switch is latched; only Harsha can reset it, by hand")
    _mood(day, steps)
    _results(day, steps)

    since, until = news_window(day.as_of, day.calendar)
    state = {"as_of": day.stamp, "phase": ph, "since": f"{since:%Y-%m-%d %H:%M}",
             "until": f"{until:%Y-%m-%d %H:%M}", "entries": not why_not, "why_not": why_not}
    if why_not:
        steps.append(Step("swarm brief", SKIPPED, "no new entries today, so the swarm is not needed"))
    else:
        steps.append(brief(day, state))
    steps.append(_alerts(day, steps))
    state["steps"] = [s.to_json() for s in steps]
    state["notes"] = list(day.notes)
    day.dir.mkdir(parents=True, exist_ok=True)
    day.path("day.json").write_text(json.dumps(state, indent=2) + "\n")
    return state


def _alerts(day: Day, steps: list[Step]) -> Step:
    """Phone alerts for what changed since the last run. A channel that fails never stops the loop."""
    try:
        d = alerts.dispatch(day.ledger, day.state_dir, day.config.alerts, f"{day.as_of:%Y-%m-%d}",
                            [s.to_json() for s in steps], send=day.alert_sender)
    except Exception as e:  # noqa: BLE001
        return Step("alerts", FAILED, f"{type(e).__name__}: {e}")
    channel = day.config.alerts.channel
    where = "logged only (channel none)" if channel == "none" else f"via {channel}"
    return Step("alerts", FAILED if d.failed and not d.sent else OK, f"{where}: {d.detail()}")


def _portfolio(day: Day, steps: list[Step]) -> Portfolio | None:
    if not day.has_account():
        steps.append(Step("paper account", BLOCKED, f"no opening balance in {day.ledger}"))
        return None
    out = day.path("portfolio.json")
    day.dir.mkdir(parents=True, exist_ok=True)
    rc, text = _run(paper_main, [*day.common_args(), "--ledger", day.ledger, "portfolio",
                                 "--as-of", day.stamp, "--out", out])
    if rc != 0:
        steps.append(Step("paper account", FAILED, text.splitlines()[-1] if text else f"exit {rc}"))
        return None
    p = Portfolio.load(out)
    steps.append(Step("paper account", OK, f"equity {p.equity:,.0f}, cash {p.cash:,.0f}, "
                                           f"{len(p.positions)} open position(s)"))
    return p


def _kill_switch(day: Day, portfolio: Portfolio | None, steps: list[Step]) -> bool:
    """R5. Latches here too, not only when an order is checked. Never resets."""
    switch = KillSwitch(day.state_dir)
    if not switch.latched and portfolio is not None and portfolio.week_open_equity > 0:
        dd = (portfolio.week_open_equity - portfolio.equity) / portfolio.week_open_equity * 100
        if dd >= day.risk.weekly_kill_pct:
            switch.latch(f"equity {portfolio.equity:,.0f} is {dd:.2f}% under the week's open "
                         f"{portfolio.week_open_equity:,.0f} (found by the daily loop)")
        else:
            steps.append(Step("kill switch", OK, f"not latched; week drawdown {max(dd, 0):.2f}% "
                                                 f"(latches at {day.risk.weekly_kill_pct}%)"))
            return False
    if switch.latched:
        steps.append(Step("kill switch", BLOCKED, f"LATCHED: {switch.state().get('reason', 'no reason recorded')}"))
        return True
    steps.append(Step("kill switch", OK, "not latched"))
    return False


def _mood(day: Day, steps: list[Step]) -> None:
    try:
        mood = regime.from_router(Router(day.data), day.as_of)
    except Exception as e:
        steps.append(Step("market mood", FAILED, f"{e}; the gate keeps full risk and says the mood is unchecked"))
        return
    day.dir.mkdir(parents=True, exist_ok=True)
    day.path("mood.json").write_text(json.dumps(mood.to_json(), indent=2) + "\n")
    steps.append(Step("market mood", OK, mood.text().splitlines()[0]))


def _results(day: Day, steps: list[Step]) -> None:
    """R9: say how many companies have a results date, so a blackout that checks nothing is never silent."""
    manual = day.state_dir / "results_dates.csv"
    try:
        dates = results.from_router(Router(day.data), day.as_of, manual)
    except ValueError as e:
        steps.append(Step("results dates", FAILED, f"{e}; R9 can't see any results date until it's fixed"))
        return
    if not dates:
        if day.risk.require_results_date:
            why = "so R9 blocks every new entry (require_results_date is on)"
        else:
            why = "so R9 blocks nothing (require_results_date is off)"
            day.note(f"no results dates known, {why}: type them into {manual} (symbol,date) or run "
                     "`swingbot-refresh --accept-nse-terms` for the NSE board meeting notices")
        steps.append(Step("results dates", OK, f"none known, {why}"))
        return
    today = day.as_of.date()
    near = sorted(t for t, d in dates.items()
                  if day.calendar.sessions_between(today, d) <= day.risk.results_blackout_sessions)
    blackout = f"in blackout today: {', '.join(near)}" if near else "none in blackout today"
    steps.append(Step("results dates", OK, f"{len(dates)} compan{'y' if len(dates) == 1 else 'ies'} with a "
                                           f"date ahead; {blackout}"))


def select_events(events: list, per_stock: int = 0, skip_roundups: bool = False, min_tone: float = 0.0) -> list:
    """The day's news the swarm sees, oldest first.

    A roundup is a headline filed under more than one stock (the matcher gives
    each named stock its own row). `min_tone` is a free screen before the
    swarm: it drops headlines whose keyword tone (the lexicon engine's
    `news_tone`) is weaker than this, which are mostly news the swarm calls
    neutral anyway. `per_stock` then keeps each stock's earliest headlines:
    most of a day's stories on one stock are the same story retold.
    """
    events = sorted(events, key=lambda e: e.published_at)
    if min_tone > 0:
        events = [e for e in events if abs(news_tone(f"{e.headline} {e.headline} {e.body}")) >= min_tone]
    if skip_roundups:
        tickers: dict[str, set[str]] = {}
        for e in events:
            tickers.setdefault(_headline_key(e.headline), set()).add(e.ticker)
        events = [e for e in events if len(tickers[_headline_key(e.headline)]) == 1]
    if per_stock > 0:
        seen: dict[str, int] = {}
        kept = []
        for e in events:
            if seen.get(e.ticker, 0) < per_stock:
                seen[e.ticker] = seen.get(e.ticker, 0) + 1
                kept.append(e)
        events = kept
    return events


def _headline_key(headline: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in headline.lower()).split())


def _selecting(config: DailyConfig) -> bool:
    return config.per_stock > 0 or config.skip_roundups or config.min_tone > 0


def window_events(day: Day, state: dict) -> list:
    if not day.data.events_file.exists():
        return []
    since, until = ist(state["since"]), ist(state["until"])
    if day.config.news_cutoff:
        since = max(since, ist(day.config.news_cutoff))
    events = [e for e in load_events(day.data.events_file) if since <= e.published_at < until]
    return select_events(events, day.config.per_stock, day.config.skip_roundups, day.config.min_tone)


def _selected_file(day: Day, events: list) -> Path:
    """The day's chosen events as their own file, so the brief shows exactly those."""
    keep = {e.event_id for e in events}
    with open(day.data.events_file, newline="") as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if r.get("event_id") in keep]
        fields = reader.fieldnames or []
    path = day.path("events.csv")
    day.dir.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def brief(day: Day, state: dict) -> Step:
    """Write the next batch of today's news for the swarm (events with views are skipped)."""
    if not day.data.events_file.exists():
        return Step("swarm brief", FAILED, f"no events file at {day.data.events_file}; the data refresh builds it")
    events = window_events(day, state)
    if not events:
        return Step("swarm brief", OK, f"no news between {state['since']} and {state['until']}: nothing to trade today")
    prices = str(day.data.prices_dir)
    source = _selected_file(day, events) if _selecting(day.config) else day.data.events_file
    argv = ["--events", source, "--views", day.path("views.jsonl"), "--limit", day.config.batch,
            "--out", day.path("brief.md"), "--cutoff", day.config.news_cutoff or "none", "--since", state["since"], "--until", state["until"]]
    if day.config.with_technicals:
        argv += ["--with-technicals", prices]
    if day.config.with_mood:
        argv += ["--with-mood", prices]
    if day.config.with_context:
        argv += ["--with-context"]
    if day.config.with_lessons and day.config.memory_db.exists():
        argv += ["--with-lessons", day.config.memory_db]
    rc, text = _run(briefs_main, argv)
    if rc != 0:
        return Step("swarm brief", FAILED, text.splitlines()[-1] if text else f"exit {rc}")
    return Step("swarm brief", OK, f"{len(events)} news item(s) since {state['since']}; {text.splitlines()[-1]}")


def load_state(day: Day) -> dict:
    path = day.path("day.json")
    if not path.exists():
        raise FileNotFoundError(f"no {path}: run `swingbot-daily start` first today")
    return json.loads(path.read_text())


# --- finish --------------------------------------------------------------


def finish(day: Day) -> dict:
    state = load_state(day)
    steps: list[Step] = []
    why_not = list(state["why_not"])
    ph = phase(day.as_of, day.calendar)
    if state["entries"] and ph in (HOLIDAY, CLOSED):
        why_not.append("today's session is over, so a DAY entry can no longer be placed")
    if state["entries"] and KillSwitch(day.state_dir).latched:
        why_not.append("the weekly kill switch latched after start")
    if ph == OPEN_NOW and state["entries"] and not why_not:
        day.note("the market is open: today's entries miss the opening auction and fill only if the "
                 "price trades under the limit later in the day")

    signals: list[dict] = []
    tickets: list[dict] = []
    if why_not:
        steps.append(Step("new entries", SKIPPED, "; ".join(why_not)))
    else:
        signals = _signals(day, state, steps)
        portfolio = Portfolio.load(day.path("portfolio.json"))
        mood_file = day.path("mood.json")
        mood = json.loads(mood_file.read_text()) if mood_file.exists() else None
        orders = day.hooks["signal_to_order"](day, signals, portfolio, mood) if signals else []
        steps.append(Step("signal to order", OK, f"{len(orders)} order(s) from {len(signals)} signal(s)"))
        tickets = _gate(day, orders, signals, steps)
        steps.append(_hook(day, "broker_submit", tickets))

    report = _report(day, steps)
    _verify(day, steps)
    steps.append(_alerts(day, steps))
    result = {"as_of": day.stamp, "phase": ph, "entries": not why_not, "why_not": why_not,
              "signals": signals, "tickets": tickets, "steps": [s.to_json() for s in steps],
              "notes": list(day.notes)}
    day.path("finish.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    day.path("summary.md").write_text(summary(state, result, report))
    return result


def _signals(day: Day, state: dict, steps: list[Step]) -> list[dict]:
    events = window_events(day, state)
    views = day.path("views.jsonl")
    if not events:
        steps.append(Step("swarm signals", OK, "no news today"))
        return []
    if not views.exists():
        steps.append(Step("swarm signals", FAILED, f"{len(events)} news item(s) but no {views}: play the swarm first"))
        return []
    swarm = RecordedSwarm(views)
    out, missing = [], []
    for e in events:
        try:
            s = swarm.simulate(e)
        except MissingViews:
            missing.append(e.event_id)
            continue
        out.append({"event_id": e.event_id, "ticker": s.ticker, "published_at": e.published_at.isoformat(),
                    # The swarm made the call this morning; the news window already limits how old the news is.
                    "as_of": state["as_of"],
                    "headline": e.headline, "sentiment": s.sentiment.value, "confidence": round(s.confidence, 4),
                    "score": round(s.score, 4), "rationale": s.rationale})
    if missing:
        day.note(f"{len(missing)} news item(s) had no views and were left out: {', '.join(missing)}")
    day.path("signals.json").write_text(json.dumps(out, indent=2) + "\n")
    counts = {k: sum(s["sentiment"] == k for s in out) for k in ("bullish", "neutral", "bearish")}
    steps.append(Step("swarm signals", OK, f"{len(out)} signal(s): " + ", ".join(f"{v} {k}" for k, v in counts.items())))
    return out


def _gate(day: Day, orders: list[Order], signals: list[dict], steps: list[Step]) -> list[dict]:
    """Check each order in turn against the account plus the entries already PASSed today."""
    pending = json.loads(day.path("portfolio.json").read_text())
    tickets = []
    for i, order in enumerate(orders, 1):
        name = f"gate {order.ticker}"
        if KillSwitch(day.state_dir).latched:
            steps.append(Step(name, BLOCKED, "the kill switch is latched; no more entries today"))
            continue
        order_file = day.path(f"orders/{i:02d}-{order.ticker}.json")
        ticket_file = day.path(f"tickets/{i:02d}-{order.ticker}.json")
        held_file = day.path("portfolio-pending.json")
        order_file.parent.mkdir(parents=True, exist_ok=True)
        order_file.write_text(json.dumps(order.to_json(), indent=2) + "\n")
        held_file.write_text(json.dumps(pending, indent=2) + "\n")
        argv = [*day.common_args(), "check", order_file, "--portfolio", held_file, "--as-of", day.stamp]
        if day.path("mood.json").exists():
            argv += ["--mood", day.path("mood.json")]
        rc, text = _run(risk_main, argv)
        if rc != 0:
            failing = [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("BLOCK", "error", "refused"))]
            steps.append(Step(name, BLOCKED if rc == 1 else FAILED, "; ".join(failing) or text))
            continue
        rc, text = _run(risk_main, [*day.common_args(), "ticket", order_file, "--as-of", day.stamp,
                                    "--out", ticket_file])
        if rc != 0:
            steps.append(Step(name, BLOCKED, text.splitlines()[-1] if text else f"exit {rc}"))
            continue
        ticket = json.loads(ticket_file.read_text())
        tickets.append(ticket)
        _link(day, order, ticket, signals)
        pending["cash"] -= ticket["amount"]
        pending["positions"].append({"ticker": order.ticker, "qty": order.qty, "entry": order.entry,
                                     "stop": order.stop, "sector": order.sector})
        steps.append(Step(name, OK, "PASS, ticket written: " + describe_ticket(ticket).splitlines()[0].strip()))
    return tickets


def _link(day: Day, order: Order, ticket: dict, signals: list[dict]) -> None:
    """Remember which news an entry came from, so the Verifier can see it when the trade closes."""
    call = (order.plan or {}).get("call")  # signal-to-order names the event it sized, when it can
    mine = [s for s in signals if s["event_id"] == call] or [s for s in signals if s["ticker"] == order.ticker]
    signal = max(mine, key=lambda s: s["confidence"]) if mine else None
    log = day.state_dir / ENTRIES_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        fh.write(json.dumps({"fingerprint": ticket["fingerprint"], "day": f"{day.as_of:%Y-%m-%d}",
                             "ticker": order.ticker, "signal": signal}) + "\n")


def _report(day: Day, steps: list[Step]) -> str:
    if not day.has_account():
        steps.append(Step("ledger report", SKIPPED, "no paper account yet"))
        return ""
    rc, text = _run(paper_main, [*day.common_args(), "--ledger", day.ledger, "report", "--as-of", day.stamp,
                                 "--out", day.path("report")])
    status = {0: OK, 1: BLOCKED}.get(rc, FAILED)
    first = text.splitlines()[0] if text else f"exit {rc}"
    steps.append(Step("ledger report", status, first + (": rule breaks found, see the report" if rc == 1 else "")))
    return text


def _verify(day: Day, steps: list[Step]) -> None:
    """Put trades that closed into the learning memory and write the Verifier brief."""
    if not day.has_account():
        steps.append(Step("trade verifier", SKIPPED, "no paper account yet"))
        return
    with redirect_stderr(io.StringIO()):
        rp, _ = _replay(SimpleNamespace(as_of=day.stamp, data_config=day.config.data_config,
                                        state_dir=str(day.state_dir)),
                        Ledger(day.ledger), day.risk)
    records = corporate.adjust_records(Ledger(day.ledger).records(), corporate.load(day.state_dir))
    links = _links(day)
    store = MemoryStore(day.config.memory_db)
    try:
        known = {r["trade_id"] for r in store.trades()}
        new = 0
        for t in rp.trades:
            trade = _closed_trade(t, records, links)
            new += trade.trade_id not in known
            store.record_trade(trade)
        waiting = len(store.trades(closed_before=day.as_of.to_pydatetime(), without_lesson=True))
    finally:
        store.close()
    if not waiting:
        steps.append(Step("trade verifier", OK, f"{new} newly closed trade(s); none waiting for a lesson"))
        return
    rc, text = _run(memory_main, ["--db", day.config.memory_db, "review", "--closed-before", day.stamp,
                                  "--out", day.path("verify.md")])
    steps.append(Step("trade verifier", OK if rc == 0 else FAILED,
                      f"{new} newly closed trade(s); {text.splitlines()[-1] if text else ''}"))


def _links(day: Day) -> dict[str, dict]:
    log = day.state_dir / ENTRIES_LOG
    if not log.exists():
        return {}
    return {r["fingerprint"]: r for r in map(json.loads, filter(str.strip, log.read_text().splitlines()))}


def _closed_trade(t, records: list[dict], links: dict[str, dict]) -> ClosedTrade:
    buy = next((r for r in records if r["kind"] == "fill" and r.get("side") == "buy"
                and r["ticker"].upper() == t.ticker and str(r["at"])[:10] == t.opened), {})
    signal = (links.get(buy.get("fingerprint", "")) or {}).get("signal") or {}
    opened = pd.Timestamp(t.opened).date()
    closed_at = datetime.combine(pd.Timestamp(t.closed).date(), MARKET_CLOSE, tzinfo=IST)
    published = (ist(signal["published_at"]).to_pydatetime() if signal
                 else datetime.combine(opened, MARKET_CLOSE, tzinfo=IST).replace(hour=9, minute=15))
    return ClosedTrade(
        run="paper", mode="paper",
        event_id=signal.get("event_id") or f"{t.ticker}-{t.opened}",
        ticker=t.ticker, published_at=min(published, closed_at),
        headline=signal.get("headline") or "(a paper trade with no linked news)",
        sentiment=signal.get("sentiment") or "bullish",
        confidence=signal.get("confidence"), score=signal.get("score"), rationale=signal.get("rationale") or "",
        entry_date=t.opened, exit_date=t.closed, closed_at=closed_at,
        ret=t.exit / t.entry - 1 if t.entry else None,
        trade_ret=t.pnl / (t.qty * t.entry) if t.qty and t.entry else None,
        hit=t.pnl > 0,
        extra={"pnl": t.pnl, "r_multiple": t.r_multiple, "qty": t.qty, "fingerprint": buy.get("fingerprint")},
    )


# --- the day's summary ---------------------------------------------------


def summary(state: dict, result: dict, report: str) -> str:
    lines = [f"# Paper trading day {state['as_of'][:10]}", "",
             f"Started {state['as_of'][11:]} IST ({state['phase'].replace('_', ' ')}), "
             f"finished {result['as_of'][11:]} IST.", ""]
    if result["entries"]:
        lines.append(f"New entries: considered, from news since {state['since']}.")
    else:
        lines.append("New entries: none today, because " + "; ".join(result["why_not"]) + ".")
    lines += ["", "## Steps", ""]
    lines += [f"- {Step(**s).line()}" for s in state["steps"] + result["steps"]]
    notes = list(dict.fromkeys(state.get("notes", []) + result["notes"]))
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    if result["signals"]:
        lines += ["", "## Signals", "", "| Event | Stock | Call | Confidence | Headline |", "|---|---|---|---|---|"]
        lines += [f"| {s['event_id']} | {s['ticker']} | {s['sentiment']} | {s['confidence']:.2f} | {s['headline']} |"
                  for s in result["signals"]]
    if result["tickets"]:
        lines += ["", "## Paper tickets (not sent anywhere)", ""]
        lines += ["```", *(describe_ticket(t) for t in result["tickets"]), "```"]
    if report:
        lines += ["", "## Account", "", "```", report, "```"]
    return "\n".join(lines) + "\n"
