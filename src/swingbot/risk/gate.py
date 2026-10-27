"""The risk gate: PASS or BLOCK for one order, with every rule's reason.

This is the hard boundary in the build plan: the swarm proposes, and only an
order that passes here may go on to the (paper, later live) broker. Rules R1
to R9 and R11 (market mood) are checked; R10 (order rate) and the 60-second stop confirmation in R1
belong to the broker adapter, which does not exist yet.

Getting out is never blocked: a sell that only reduces a holding passes every
limit (only a sell of more than is held, which would be a short, is blocked).
That and the order fingerprint come from day-trader-skill's trader-risk (MIT,
Jake Schincariol); the rules themselves are the build plan's.

The weekly kill switch (R5) latches in a state file. It latches whenever the
account is looked at with equity 5% under the week's open: on any order check
(a sell too), `swingbot-risk kill-switch --portfolio`, and `swingbot-paper
report` or `portfolio`. Once set, every entry is blocked until Harsha resets it
by hand (`swingbot-risk kill-switch --reset`), which asks for the reset to be
typed at a terminal and refuses to run under Claude Code. The bot and Claude
never reset it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .calendar import NSECalendar
from .config import RiskConfig
from .portfolio import Order, Portfolio
from .sizing import gap_risk_per_share

DEFAULT_STATE_DIR = Path("data/state")


@dataclass(frozen=True)
class Check:
    rule: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    order: Order
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trip_kill_switch: str = ""  # set when the account is past the weekly limit (sells too)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "BLOCK"

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "fingerprint": self.order.fingerprint,
            "order": self.order.to_json(),
            "checks": [c.__dict__ for c in self.checks],
            "warnings": self.warnings,
        }

    def text(self) -> str:
        o = self.order
        lines = [f"{self.status}  {o.side} {o.qty} {o.ticker} @ {o.entry:,.2f}  [{o.fingerprint}]"]
        for c in self.checks:
            lines.append(f"  {'ok   ' if c.ok else 'BLOCK'} {c.rule}: {c.detail}")
        lines += [f"  note  {w}" for w in self.warnings]
        return "\n".join(lines)


class ResetRefused(Exception):
    """A kill switch reset that did not come from a person at a terminal."""


def weekly_breach(portfolio: Portfolio, config: RiskConfig) -> str:
    """Why R5 trips for this account, or "" when it does not."""
    week = portfolio.week_open_equity
    dd = (week - portfolio.equity) / week * 100 if week > 0 else 0.0
    if dd < config.weekly_kill_pct:
        return ""
    return f"equity {portfolio.equity:,.0f} is {dd:.2f}% under the week's open {week:,.0f}"


def week_of(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def run_by_claude() -> bool:
    """True inside Claude Code (its shell sets CLAUDECODE) or any agent without a terminal."""
    return bool(os.environ.get("CLAUDECODE")) or not sys.stdin.isatty()


class KillSwitch:
    """R5's latch, kept in `<state_dir>/kill_switch.json`."""

    def __init__(self, state_dir: str | Path = DEFAULT_STATE_DIR):
        self.path = Path(state_dir) / "kill_switch.json"

    def state(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {"latched": False}

    @property
    def latched(self) -> bool:
        return bool(self.state().get("latched"))

    def latch(self, reason: str, week: str = "") -> bool:
        """Latch it; True if this call did. A breach in a week Harsha already
        cleared, or an earlier one, does not latch it again."""
        st = self.state()
        if st.get("latched") or (week and week <= st.get("cleared_week", "")):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"latched": True, "reason": reason, "week": week,
             "at": datetime.now().isoformat(timespec="seconds")}, indent=2))
        return True

    def update(self, portfolio: Portfolio, config: RiskConfig, today: date) -> bool:
        """Latch it if the account is past the weekly limit; True if this call did."""
        reason = weekly_breach(portfolio, config)
        return bool(reason) and self.latch(reason, week_of(today))

    def reset(self, by: str, today: date | None = None) -> None:
        """For Harsha, by hand. The breach's week stays cleared, so the same
        drawdown does not latch it again; the gate still blocks entries while
        the week is 5% down."""
        if run_by_claude():
            raise ResetRefused("the kill switch is reset only by Harsha, typing it at a terminal; "
                               "Claude and the bot never reset it")
        st = self.state()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"latched": False, "reset_by": by, "cleared_week": st.get("week") or week_of(today or date.today()),
             "cleared_reason": st.get("reason", ""), "at": datetime.now().isoformat(timespec="seconds")},
            indent=2))


def _pct(x: float) -> str:
    return f"{x:.2f}%"


def check_order(order: Order, portfolio: Portfolio, config: RiskConfig | None = None, *,
                bars: pd.DataFrame | None = None, today: date | None = None,
                results_date: date | None = None, calendar: NSECalendar | None = None,
                kill_switch_latched: bool = False, last_price: float | None = None,
                mood: dict | None = None) -> Verdict:
    """Check one order. `bars` must be cut to what is known today (for R8).

    `mood` is a `swingbot-risk mood` result (default: the one the order was
    sized under). A defensive verdict cuts R2's limit by `defensive_risk_scale`.
    """
    config = config or RiskConfig()
    calendar = calendar or NSECalendar()
    v = Verdict(order)
    add = lambda rule, ok, detail: v.checks.append(Check(rule, bool(ok), detail))  # noqa: E731
    eq = portfolio.equity

    # Format and side, for entries and exits alike.
    if order.side not in ("buy", "sell") or order.qty < 1 or order.entry <= 0:
        add("format", False, "side must be buy or sell, qty a whole number >= 1, price above 0")
        return v
    if order.product != "CNC":
        add("format", False, f"product {order.product!r}: only CNC (delivery) is allowed, no intraday or margin")
        return v
    v.trip_kill_switch = weekly_breach(portfolio, config)
    held = portfolio.held(order.ticker)
    if order.side == "sell":
        add("long only", order.qty <= held,
            f"selling {order.qty} of {held} held" + ("" if order.qty <= held else ": that would be a short"))
        if order.qty <= held:
            v.warnings.append("an exit: no other limit applies, getting out is never blocked")
        return v

    # Entries from here on.
    if kill_switch_latched:
        add("R5 weekly kill switch", False, "latched; only a manual reset by Harsha clears it")
    week_dd = (portfolio.week_open_equity - eq) / portfolio.week_open_equity * 100
    if v.trip_kill_switch:
        add("R5 weekly kill switch", False, v.trip_kill_switch + "; latching")
    elif not kill_switch_latched:
        add("R5 weekly kill switch", True, f"week drawdown {_pct(max(week_dd, 0))} (limit {config.weekly_kill_pct}%)")
    day_dd = (portfolio.day_open_equity - eq) / portfolio.day_open_equity * 100
    add("R6 daily loss", day_dd < config.daily_loss_pct,
        f"today {_pct(-day_dd)} vs the open (entries pause at -{config.daily_loss_pct}%)")

    if held and not config.allow_adding:
        add("adding", False, f"already holding {held} {order.ticker}; adding to a position is off")

    stop, target = order.stop, order.target
    if stop is None or target is None:
        add("R1 stop and target", False, "every entry needs both a stop and a target")
        return v
    entry = max(order.entry, last_price or 0.0)
    if last_price and last_price > order.entry:
        v.warnings.append(f"price {last_price:,.2f} is above the plan's entry; risk is measured from it")
    if not stop < entry < target:
        add("R1 stop and target", False, f"needs stop < entry < target, got {stop} / {entry} / {target}")
        return v
    add("R1 stop and target", True, f"stop {stop:,.2f}, target {target:,.2f}")

    rr = (target - entry) / (entry - stop)
    add("reward:risk", rr >= config.min_reward_risk, f"{rr:.2f} (minimum {config.min_reward_risk})")

    risk_pct = _market_mood(add, v, mood if mood is not None else order.mood, today, config)
    loss = order.qty * gap_risk_per_share(entry, stop, config)
    limit = eq * risk_pct / 100
    add("R2 risk per trade", loss <= limit + 1e-6,
        f"loss at the stop with a {config.gap_allowance_pct}% gap {loss:,.0f} "
        f"= {_pct(loss / eq * 100)} of equity (limit {risk_pct:g}%"
        + (", cut for the defensive mood)" if risk_pct < config.risk_pct else ")"))

    notional = order.qty * entry
    add("R3 position cap", notional <= eq * config.max_position_pct / 100 + 1e-6,
        f"{notional:,.0f} = {_pct(notional / eq * 100)} of equity (limit {config.max_position_pct}%)")
    spendable = portfolio.cash - eq * config.cash_buffer_pct / 100
    add("cash", notional <= spendable + 1e-6, f"needs {notional:,.0f}, {spendable:,.0f} available (no margin)")

    tickers = {p.ticker for p in portfolio.positions} | {order.ticker}
    add("R4 open positions", len(tickers) <= config.max_open_positions,
        f"{len(tickers)} after this (limit {config.max_open_positions})")
    if not order.sector:
        add("R4 sector", False, f"sector unknown for {order.ticker}; add it to data/universe/sectors.csv")
    else:
        same = {p.ticker for p in portfolio.positions if p.sector == order.sector} | {order.ticker}
        add("R4 sector", len(same) <= config.max_per_sector,
            f"{len(same)} in {order.sector} after this (limit {config.max_per_sector})")
    open_risk = portfolio.open_risk + order.qty * (entry - stop)
    add("R4 open risk", open_risk <= eq * config.max_open_risk_pct / 100 + 1e-6,
        f"{open_risk:,.0f} = {_pct(open_risk / eq * 100)} of equity at the stops (limit {config.max_open_risk_pct}%)")

    _trend_filter(add, bars, config)
    _results_blackout(add, v, today, results_date, calendar, config)
    return v


def _market_mood(add, v: Verdict, mood: dict | None, today: date | None, config: RiskConfig) -> float:
    """R11: the market mood. Returns R2's limit in % of equity."""
    if not mood:
        v.warnings.append("market mood not checked; run `swingbot-risk mood` and pass --mood")
        return config.risk_pct
    verdict, as_of = mood.get("verdict"), str(mood.get("as_of", ""))
    if today and not as_of.startswith(f"{today:%Y-%m-%d}"):
        v.warnings.append(f"market mood is from {as_of or 'an unknown time'}, not {today}; run it again")
    if verdict == "unknown":
        v.warnings.append("market mood unknown (too few series); risk per trade not cut")
    risk_pct = config.risk_pct_for(verdict)
    if verdict == "defensive" and risk_pct <= 0:
        add("R11 market mood", False, "defensive, and defensive_risk_scale is 0: no new entries")
    elif verdict == "defensive":
        add("R11 market mood", True, f"defensive: risk per trade cut to {risk_pct:g}% of equity")
    else:
        add("R11 market mood", True, f"{verdict}: full risk per trade")
    return risk_pct


def _trend_filter(add, bars: pd.DataFrame | None, config: RiskConfig) -> None:
    n = config.trend_sma
    if bars is None or len(bars) < n:
        have = 0 if bars is None else len(bars)
        add("R8 trend filter", False, f"needs {n} sessions of price history, have {have}")
        return
    close = float(bars["close"].iloc[-1])
    sma = float(bars["close"].iloc[-n:].mean())
    add("R8 trend filter", close >= sma, f"close {close:,.2f} vs {n}-day SMA {sma:,.2f}")
    value_cr = float((bars["close"] * bars["volume"]).iloc[-20:].mean()) / 1e7
    add("R8 liquidity", value_cr >= config.min_traded_value_cr,
        f"20-day average traded value {value_cr:,.1f} crore (minimum {config.min_traded_value_cr})")


def _results_blackout(add, v: Verdict, today: date | None, results_date: date | None,
                      calendar: NSECalendar, config: RiskConfig) -> None:
    if results_date is None:
        msg = "next results date unknown; pass --results-date to check the blackout"
        if config.require_results_date:
            add("R9 results blackout", False, msg)
        else:
            v.warnings.append(msg)
        return
    today = today or date.today()
    if results_date < today:
        add("R9 results blackout", True, f"results on {results_date} are already out")
        return
    for year in calendar.unknown_years(today, results_date):
        v.warnings.append(f"no NSE holiday list for {year}; counting every weekday as a session")
    n = calendar.sessions_between(today, results_date)
    add("R9 results blackout", n > config.results_blackout_sessions,
        f"results on {results_date}, {n} sessions away (no entry within {config.results_blackout_sessions})")
