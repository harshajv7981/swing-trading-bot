"""Position sizing: whole shares from the stop distance (build plan R2, R3, R4).

Shares are the largest whole number that fits every limit at once, and the
result names the limit that decided it. A stop too far away for even one share
is refused, never shrunk into a different trade. The "say which limit decided
it" idea is from day-trader-skill's trader-budget (MIT, Jake Schincariol).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import RiskConfig
from .exits import ExitPlan
from .portfolio import Order, Portfolio


class SizingRefused(ValueError):
    """No whole share fits. The message says which limit and why."""


@dataclass(frozen=True)
class Sizing:
    order: Order
    binding: str            # the limit that decided the quantity
    limits: dict[str, int]  # shares each limit alone would allow
    loss_at_stop: float     # qty x (entry - stop)
    loss_with_gap: float    # the same with the gap allowance, what R2 measures
    risk_pct: float = 0.0   # R2's limit used, after any market-mood cut (R11)


def gap_risk_per_share(entry: float, stop: float, config: RiskConfig) -> float:
    """Loss per share if the stop fills `gap_allowance_pct` below its price (R9)."""
    return entry - stop * (1 - config.gap_allowance_pct / 100)


def size_position(plan: ExitPlan, portfolio: Portfolio, config: RiskConfig | None = None,
                  sector: str = "", mood: dict | None = None) -> Sizing:
    """`mood` is a `swingbot-risk mood` result; a defensive verdict cuts the risk per trade (R11)."""
    config = config or RiskConfig()
    eq, entry = portfolio.equity, plan.entry
    verdict = (mood or {}).get("verdict")
    risk_pct = config.risk_pct_for(verdict)
    if risk_pct <= 0:
        raise SizingRefused(f"the market mood is defensive (as of {mood.get('as_of')}) and "
                            "defensive_risk_scale is 0, so new entries are off")
    per_share = gap_risk_per_share(entry, plan.stop, config)
    if eq <= 0 or per_share <= 0:
        raise SizingRefused("equity and the stop distance must both be positive")

    room = eq * config.max_open_risk_pct / 100 - portfolio.open_risk
    spendable = portfolio.cash - eq * config.cash_buffer_pct / 100
    limits = {
        "risk per trade": math.floor(eq * risk_pct / 100 / per_share),
        "position cap": math.floor(eq * config.max_position_pct / 100 / entry),
        "cash": math.floor(max(spendable, 0) / entry),
        "open risk room": math.floor(max(room, 0) / per_share),
    }
    binding = min(limits, key=limits.get)
    qty = limits[binding]
    if qty < 1:
        reasons = {
            "risk per trade": f"one share would lose {per_share:,.2f} at the stop (with the gap allowance), "
                              f"more than {risk_pct:g}% of equity ({eq * risk_pct / 100:,.0f}); "
                              "the stop is too far away for this account",
            "position cap": f"one share at {entry:,.2f} is over {config.max_position_pct}% of equity",
            "cash": f"cash after the buffer ({max(spendable, 0):,.0f}) does not cover one share at {entry:,.2f}",
            "open risk room": f"open risk is already {portfolio.open_risk:,.0f}, leaving no room under "
                              f"{config.max_open_risk_pct}% of equity",
        }
        raise SizingRefused(reasons[binding])

    order = Order(plan.ticker, "buy", qty, entry, plan.stop, plan.target, sector, plan=plan.to_json(),
                  mood=dict(mood or {}))
    return Sizing(order, binding, limits, round(qty * (entry - plan.stop), 2), round(qty * per_share, 2),
                  risk_pct)
