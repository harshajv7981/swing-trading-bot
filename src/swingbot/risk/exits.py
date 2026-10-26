"""Exit plans: where the stop and target go before an entry, and how the stop trails.

The idea of a separate exit planner that refuses a bad stop in one sentence
comes from Jake Schincariol's day-trader-skill (MIT,
github.com/Jakeschincariol/day-trader-skill, trader-exit). The code is our own
and works on daily NSE bars for multi-day holds, long side only.

Stop rules:   atr:K (K x ATR under the entry), pct:X, price:X, low:N (under the
              lowest low of the last N sessions).
Target rules: 2R (a multiple of the stop distance) or price:X.
Prices round DOWN to the tick: a stop a tick lower and a target a tick lower
are both the conservative side.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd

from ..technicals import _wilder
from .config import RiskConfig


class PlanRefused(ValueError):
    """A plan that does not make sense. The message is one plain sentence."""


@dataclass(frozen=True)
class ExitPlan:
    ticker: str
    as_of: str  # the last session whose bar the plan used, YYYY-MM-DD
    entry: float
    stop: float
    stop_rule: str
    target: float
    target_rule: str
    atr: float

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop

    @property
    def reward_risk(self) -> float:
        return (self.target - self.entry) / self.risk_per_share

    def to_json(self) -> dict:
        return {**asdict(self), "risk_per_share": round(self.risk_per_share, 2),
                "reward_risk": round(self.reward_risk, 2)}

    @classmethod
    def from_json(cls, d: dict) -> "ExitPlan":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def round_down(price: float, tick: float) -> float:
    return round(math.floor(price / tick + 1e-9) * tick, 2)


def atr(bars: pd.DataFrame, n: int = 14) -> float | None:
    if len(bars) <= n:
        return None
    high, low, close = bars["high"], bars["low"], bars["close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    value = _wilder(tr, n).iloc[-1]
    return None if pd.isna(value) else float(value)


def _number(rule: str, text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise PlanRefused(f"cannot read the number in {rule!r}") from None


def _raw_stop(rule: str, entry: float, bars: pd.DataFrame, atr_value: float, tick: float) -> float:
    kind, _, arg = rule.partition(":")
    if kind == "atr":
        return entry - _number(rule, arg) * atr_value
    if kind == "pct":
        return entry * (1 - _number(rule, arg) / 100)
    if kind == "price":
        return _number(rule, arg)
    if kind == "low":
        n = int(_number(rule, arg))
        if len(bars) < n:
            raise PlanRefused(f"{rule!r} needs {n} sessions of history, only {len(bars)} known")
        return float(bars["low"].iloc[-n:].min()) - tick
    raise PlanRefused(f"unknown stop rule {rule!r}: use atr:K, pct:X, price:X or low:N")


def _raw_target(rule: str, entry: float, risk: float) -> float:
    if rule.upper().endswith("R"):
        return entry + _number(rule, rule[:-1]) * risk
    kind, _, arg = rule.partition(":")
    if kind == "price":
        return _number(rule, arg)
    raise PlanRefused(f"unknown target rule {rule!r}: use a multiple like 2R, or price:X")


def plan_exit(ticker: str, bars: pd.DataFrame, entry: float | None = None, stop_rule: str = "atr:2",
              target_rule: str = "2R", config: RiskConfig | None = None) -> ExitPlan:
    """Stop and target for a long entry, from the bars known at decision time.

    `bars` must already be cut to what was known (technicals.bars_known_at);
    the plan never looks further. With no `entry`, the last close is used.
    """
    config = config or RiskConfig()
    if bars.empty:
        raise PlanRefused(f"no price history for {ticker}")
    atr_value = atr(bars, config.atr_period)
    if atr_value is None or atr_value <= 0:
        raise PlanRefused(f"{ticker} needs more than {config.atr_period} sessions of history for an ATR")
    entry = round(float(bars["close"].iloc[-1]) if entry is None else entry, 2)
    if entry <= 0:
        raise PlanRefused("the entry price must be positive")

    stop = round_down(_raw_stop(stop_rule, entry, bars, atr_value, config.tick), config.tick)
    if stop <= 0:
        raise PlanRefused(f"stop rule {stop_rule!r} puts the stop at or below zero")
    if stop >= entry:
        raise PlanRefused(f"the stop {stop:.2f} is at or above the entry {entry:.2f}; this bot only buys")
    risk = entry - stop
    if risk < config.min_stop_atr * atr_value:
        raise PlanRefused(
            f"the stop is {risk:.2f} under the entry, less than {config.min_stop_atr} x ATR "
            f"({atr_value:.2f}); one normal day's noise would hit it"
        )

    target = round_down(_raw_target(target_rule, entry, risk), config.tick)
    if target <= entry:
        raise PlanRefused(f"the target {target:.2f} is at or below the entry {entry:.2f}")

    return ExitPlan(ticker.upper(), f"{bars.index[-1]:%Y-%m-%d}", entry, stop, stop_rule,
                    target, target_rule, round(atr_value, 2))


def trail_stop(bars: pd.DataFrame, entry_date: str, entry: float, initial_stop: float,
               current_stop: float, config: RiskConfig | None = None) -> tuple[float, str]:
    """Build plan rule R7. Returns (new stop, why), and the new stop is never lower.

    Once a close is 1R above the entry (R = entry - initial stop), the stop goes
    to breakeven; from then on it also trails `trail_atr` x ATR under the
    highest close since entry. `bars` must hold only sessions already closed.
    """
    config = config or RiskConfig()
    held = bars[bars.index > pd.Timestamp(entry_date)]
    if held.empty:
        return current_stop, "no session has closed since the entry"
    r = entry - initial_stop
    best = float(held["close"].max())
    if best < entry + r:
        return current_stop, f"highest close {best:.2f} has not reached +1R ({entry + r:.2f})"
    candidate, why = entry, "breakeven after +1R"
    atr_value = atr(bars, config.atr_period)
    if atr_value is not None:
        trail = round_down(best - config.trail_atr * atr_value, config.tick)
        if trail > candidate:
            candidate, why = trail, f"{config.trail_atr} x ATR ({atr_value:.2f}) under the highest close {best:.2f}"
    if candidate <= current_stop:
        return current_stop, f"stop stays at {current_stop:.2f} (the rule gives {candidate:.2f}; stops never move down)"
    return candidate, why
