"""Time Machine: the honest odds of an exit plan before any money is risked.

Two answers to one question: if the next few sessions behave like this
stock's own past, how often does the plan hit its target first, its stop
first, or neither by the end of the hold?

1. **Replays.** Each replay walks the hold one session at a time. Every
   session is a random past session of the same stock, taken as four ratios to
   its previous close (open, high, low, close) and scaled to the replay's
   price. Seeded, so the same plan, bars and seed give the same numbers.
2. **Base rate.** The same shape of trade (stop k x ATR under the close,
   target at the plan's reward:risk) placed at the close of every past session,
   walked forward on the real bars that followed. It is not conditioned on any
   signal, so it is the bar a signal has to beat. Windows overlap, so the
   trades are not independent.

Swing rules, unlike a day trade: an open below the stop fills at the open (a
gap through the stop, worse than -1R), an open above the target fills at the
open, and a session that touches both counts as the stop. Costs are left out.

The idea is from day-trader-skill's trader-timemachine (MIT, Jake
Schincariol), which replays 5-minute bars inside one session. This version is
our own, on daily NSE bars over a multi-day hold. A simulation built from the
past, never a prediction: results, a halt or news can do what the past never did.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .exits import ExitPlan

MIN_SESSIONS = 120


class NotEnoughHistory(ValueError):
    pass


@dataclass(frozen=True)
class Odds:
    trades: int
    target_first: float
    stop_first: float
    neither: float
    gap_through_stop: float  # share of all trades stopped at an open below the stop
    avg_r: float

    def line(self) -> str:
        return (f"target first {self.target_first:.1%}, stop first {self.stop_first:.1%}, "
                f"neither {self.neither:.1%} (gap through the stop {self.gap_through_stop:.1%}); "
                f"average {self.avg_r:+.2f}R over {self.trades:,} trades")


@dataclass(frozen=True)
class Report:
    plan: dict
    hold: int
    seed: int
    pool_sessions: int
    replays: Odds
    close_pct: dict[str, float]  # 5th, 50th, 95th percentile of the price at the end of the hold
    base_rate: Odds | None
    verdict: str
    notes: tuple[str, ...]

    def to_json(self) -> dict:
        d = asdict(self)
        d["notes"] = list(self.notes)
        return d

    def text(self) -> str:
        p = self.plan
        lines = [
            f"{p['ticker']}: entry {p['entry']:,.2f}, stop {p['stop']:,.2f}, target {p['target']:,.2f}, "
            f"hold {self.hold} sessions, bars to {p['as_of']}",
            f"Replays (seed {self.seed}, pool of {self.pool_sessions} sessions): {self.replays.line()}",
            f"Price at the end of the hold: median {self.close_pct['p50']:,.2f}, "
            f"5 to 95% range {self.close_pct['p5']:,.2f} to {self.close_pct['p95']:,.2f}",
            "Base rate, same stop and target on every past day: "
            + (self.base_rate.line() if self.base_rate else "not enough history"),
            f"Verdict: {self.verdict}",
        ]
        lines += [f"  {n}" for n in self.notes]
        return "\n".join(lines)


def _walk(o, h, lo, c, entry, stop, target, risk):
    """Walk sessions (arrays shaped trades x hold) and return outcome codes and R.

    Codes: 1 target first, -1 stop first, -2 stop at a gap open, 0 neither.
    """
    n, hold = o.shape
    code = np.zeros(n, dtype=int)
    r = np.full(n, np.nan)
    for t in range(hold):
        live = code == 0
        gap_down = live & (o[:, t] <= stop)
        gap_up = live & ~gap_down & (o[:, t] >= target)
        hit_stop = live & ~gap_down & ~gap_up & (lo[:, t] <= stop)  # both in one session counts as the stop
        hit_target = live & ~gap_down & ~gap_up & ~hit_stop & (h[:, t] >= target)
        code[gap_down], r[gap_down] = -2, (o[gap_down, t] - entry[gap_down]) / risk[gap_down]
        code[gap_up], r[gap_up] = 1, (o[gap_up, t] - entry[gap_up]) / risk[gap_up]
        code[hit_stop], r[hit_stop] = -1, -1.0
        code[hit_target], r[hit_target] = 1, (target[hit_target] - entry[hit_target]) / risk[hit_target]
    open_ = code == 0
    r[open_] = (c[open_, -1] - entry[open_]) / risk[open_]
    return code, r


def _odds(code: np.ndarray, r: np.ndarray) -> Odds:
    n = len(code)
    return Odds(n, float(np.mean(code == 1)), float(np.mean(code < 0)), float(np.mean(code == 0)),
                float(np.mean(code == -2)), float(np.mean(r)))


def verdict(odds: Odds, reward_risk: float) -> tuple[str, list[str]]:
    """One line from the replays that hit a side, against break-even 1 / (1 + R)."""
    decided = odds.target_first + odds.stop_first
    notes: list[str] = []
    if decided == 0:
        return "no replay reached the stop or the target; the hold is too short to tell", notes
    need, got = 1 / (1 + reward_risk), odds.target_first / decided
    gap = (got - need) * 100
    if abs(gap) < 5:
        text = "close to a coin flip" if abs(reward_risk - 1) < 0.05 else "close to break-even"
    else:
        strength = "leans" if abs(gap) < 15 else "strongly"
        text = f"{strength} toward the {'target' if gap > 0 else 'stop'}"
    text += (f": the target came first in {got:.0%} of the replays that hit a side, "
             f"break-even at {reward_risk:.2f}R is {need:.0%}")
    if odds.neither > 0.4:
        notes.append(f"{odds.neither:.0%} of replays hit neither by the end of the hold; "
                     "where the price ends decides those")
    if (gap > 0) != (odds.avg_r > 0) and abs(gap) >= 5:
        notes.append(f"the average R is {odds.avg_r:+.2f}, against the lean, because of the replays that hit neither")
    if odds.gap_through_stop > 0.02:
        notes.append(f"{odds.gap_through_stop:.0%} of replays gapped through the stop at the open: "
                     "the loss there is worse than 1R")
    return text, notes


def run(plan: ExitPlan, bars: pd.DataFrame, hold: int = 10, replays: int = 10_000, seed: int = 19,
        lookback: int = 500, atr_period: int = 14) -> Report:
    """`bars` must already be cut to what was known at the plan's date."""
    if hold < 1 or replays < 1:
        raise ValueError("hold and replays must be at least 1")
    bars = bars[bars.index <= pd.Timestamp(plan.as_of)].iloc[-(lookback + 1):]
    if len(bars) < MIN_SESSIONS:
        raise NotEnoughHistory(f"{plan.ticker} has {len(bars)} sessions to {plan.as_of}; "
                               f"the Time Machine needs at least {MIN_SESSIONS}")
    prev = bars["close"].shift()
    ratios = (bars[["open", "high", "low", "close"]].div(prev, axis=0)).iloc[1:].to_numpy()

    rng = np.random.default_rng(seed)
    pick = ratios[rng.integers(0, len(ratios), size=(replays, hold))]  # replays x hold x 4
    level = plan.entry * np.cumprod(pick[:, :, 3], axis=1)
    prior = np.concatenate([np.full((replays, 1), plan.entry), level[:, :-1]], axis=1)
    o, h, lo, c = (prior * pick[:, :, i] for i in range(4))
    n = np.ones(replays)
    code, r = _walk(o, h, lo, c, plan.entry * n, plan.stop * n, plan.target * n, plan.risk_per_share * n)
    mc = _odds(code, r)
    end = c[:, -1]
    close_pct = {f"p{q}": round(float(np.percentile(end, q)), 2) for q in (5, 50, 95)}

    text, notes = verdict(mc, plan.reward_risk)
    return Report(plan.to_json(), hold, seed, len(ratios), mc, close_pct,
                  base_rate(plan, bars, hold, atr_period), text, tuple(notes))


def base_rate(plan: ExitPlan, bars: pd.DataFrame, hold: int, atr_period: int = 14) -> Odds | None:
    """The plan's stop (in ATRs) and reward:risk, entered at every past close with `hold` sessions after it."""
    k = plan.risk_per_share / plan.atr if plan.atr else None
    if not k:
        return None
    high, low, close = bars["high"], bars["low"], bars["close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_series = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean().to_numpy()
    arr = bars[["open", "high", "low", "close"]].to_numpy()
    starts = [i for i in range(atr_period, len(bars) - hold) if not np.isnan(atr_series[i])]
    if len(starts) < 20:
        return None
    idx = np.array(starts)
    window = np.stack([arr[i + 1:i + 1 + hold] for i in idx])  # trades x hold x 4
    entry = arr[idx, 3]
    risk = k * atr_series[idx]
    code, r = _walk(window[:, :, 0], window[:, :, 1], window[:, :, 2], window[:, :, 3],
                    entry, entry - risk, entry + plan.reward_risk * risk, risk)
    return _odds(code, r)


__all__ = ["NotEnoughHistory", "Odds", "Report", "base_rate", "run", "verdict"]
