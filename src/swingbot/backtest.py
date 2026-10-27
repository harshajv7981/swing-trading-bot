"""Replay news events through a swarm and measure what prices did next.

Timing rules (the part that most often makes a backtest lie):

* NSE cash market opens 09:15 IST, at a price set by the pre-open auction,
  whose order entry closes at 09:08. News published before 09:08 on a trading
  day can be acted on at that day's open. Anything later enters at the *next*
  trading day's open. We never enter at a price set before the news.
* The exit is the close of the Nth trading session, counting the entry session
  as session 1, where N = ceil(horizon_hours / 24). 72h -> 3 sessions.
* The swarm only receives the NewsEvent. It never sees prices.

Two separate scores come out of each event:

* **Directional accuracy** (`hit`): did the move to the horizon close go the way
  the call said? Bullish and bearish calls both count. This grades the swarm.
* **The trade** (`trade_ret`): what the bot would actually have made. The bot
  is long-only (CNC delivery), so a bearish call is no trade. A bullish call
  places the paper broker's ticket: a DAY limit buy at the last close, with
  the stop and target from the exit planner (risk/exits.py), built only from
  bars that had closed before the entry. It fills at the open if the stock
  opens at or under the limit, else at the limit if the low trades under it,
  else there is no trade. The stop trails per rule R7 after each close. The
  trade ends at the stop, the target or the horizon close, whichever comes
  first. `entry="open"` buys at the open instead, for comparison; the base
  rates in docs/research/signal-thresholds.md favour the limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .models import AUCTION_CUTOFF, IST, NewsEvent, Sentiment, Signal
from .prices import PriceSource
from .risk.config import RiskConfig
from .risk.exits import PlanRefused, plan_exit, trail_stop
from .swarm.base import SwarmEngine
from .swarm.recorded import MissingViews


@dataclass(frozen=True)
class Outcome:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    ret: float  # simple return, exit close / entry open - 1
    max_up: float  # best high vs entry inside the window
    max_down: float  # worst low vs entry inside the window
    bench_ret: float | None = None


def entry_index(bars: pd.DataFrame, event: NewsEvent) -> int | None:
    """Position in `bars` of the first session we could have traded."""
    ts = pd.Timestamp(event.published_at).tz_convert(IST)
    day = ts.tz_localize(None).normalize()
    on_or_after = int(bars.index.searchsorted(day, side="left"))
    if on_or_after >= len(bars):
        return None
    same_day = bars.index[on_or_after] == day
    if same_day and ts.time() >= AUCTION_CUTOFF:
        on_or_after += 1
    return on_or_after if on_or_after < len(bars) else None


def measure(
    bars: pd.DataFrame, event: NewsEvent, sessions: int, bench: pd.DataFrame | None = None
) -> Outcome | None:
    i = entry_index(bars, event)
    if i is None or i + sessions - 1 >= len(bars):
        return None
    window = bars.iloc[i : i + sessions]
    entry, exit_ = float(window["open"].iloc[0]), float(window["close"].iloc[-1])

    bench_ret = None
    if bench is not None:
        b = bench.reindex(window.index)
        if b["open"].notna().iloc[0] and b["close"].notna().iloc[-1]:
            bench_ret = float(b["close"].iloc[-1] / b["open"].iloc[0] - 1)

    return Outcome(
        entry_date=window.index[0],
        exit_date=window.index[-1],
        entry_price=entry,
        exit_price=exit_,
        ret=exit_ / entry - 1,
        max_up=float(window["high"].max()) / entry - 1,
        max_down=float(window["low"].min()) / entry - 1,
        bench_ret=bench_ret,
    )


@dataclass(frozen=True)
class LongTrade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry: float
    stop: float  # the initial stop from the exit planner
    target: float
    exit_price: float
    exit_reason: str  # stop, gap_stop, target, gap_target or time
    final_stop: float  # the stop after trailing, when the trade ended

    @property
    def ret(self) -> float:
        return self.exit_price / self.entry - 1

    @property
    def r_multiple(self) -> float:
        return (self.exit_price - self.entry) / (self.entry - self.stop)


class NotFilled(Exception):
    """The DAY limit entry never traded: no trade."""


ENTRIES = ("limit", "open")


def simulate_long(bars: pd.DataFrame, i: int, sessions: int, ticker: str = "", stop_rule: str = "atr:2",
                  target_rule: str = "2R", config: RiskConfig | None = None,
                  trail: bool = True, entry: str = "limit") -> LongTrade:
    """Buy on bar `i` and hold up to `sessions` sessions with a stop and target.

    entry="limit" (the bot's ticket): a DAY limit at the last close. It fills
    at the open if bar `i` opens at or under it, else at the limit if the low
    trades *under* it (a touch is not enough), else NotFilled is raised.
    entry="open": buys at the open, with the stop and target planned from it.

    The plan uses only bars before `i`, as the bot would have had at the open.
    Raises PlanRefused when the exit planner would not allow the trade.

    Fills, on daily bars, from the conservative side:
    * an open at or under the stop sells at the open (a gap through the stop);
    * an open at or over the target sells at the open (a gap through the target);
    * a day whose range touches both the stop and the target counts as the stop,
      since daily bars cannot say which came first;
    * otherwise the stop or target fills at its own price;
    * if neither is hit, the trade sells at the last session's close.
    """
    if entry not in ENTRIES:
        raise ValueError(f"entry must be one of {', '.join(ENTRIES)}, not {entry!r}")
    config = config or RiskConfig()
    window = bars.iloc[i : i + sessions]
    entry_date, open_ = window.index[0], float(window["open"].iloc[0])
    plan = plan_exit(ticker, bars.iloc[:i], entry=open_ if entry == "open" else None, stop_rule=stop_rule,
                     target_rule=target_rule, config=config)
    stop, target = plan.stop, plan.target
    if entry == "open" or open_ <= plan.entry:
        entry = open_ if entry == "limit" else plan.entry
    elif float(window["low"].iloc[0]) < plan.entry:
        entry = plan.entry
    else:
        raise NotFilled(f"opened at {open_:,.2f} and never traded under the limit {plan.entry:,.2f}")

    def done(day, price, reason):
        return LongTrade(entry_date, day, entry, plan.stop, target, float(price), reason, stop)

    for k, (day, bar) in enumerate(window.iterrows()):
        if k > 0 and bar["open"] <= stop:
            return done(day, bar["open"], "gap_stop")
        if k > 0 and bar["open"] >= target:
            return done(day, bar["open"], "gap_target")
        if bar["low"] <= stop:
            return done(day, stop, "stop")
        if bar["high"] >= target:
            return done(day, target, "target")
        if trail:
            stop, _ = trail_stop(bars.iloc[: i + k + 1], f"{entry_date:%Y-%m-%d}", entry, plan.stop, stop, config)
    return done(window.index[-1], window["close"].iloc[-1], "time")


def sessions_for(signal: Signal) -> int:
    return max(1, math.ceil(signal.horizon_hours / 24))


def run_backtest(
    events: list[NewsEvent],
    engine: SwarmEngine,
    prices: PriceSource,
    benchmark: str | None = None,
    cost_bps: float = 25.0,
    stop_rule: str = "atr:2",
    target_rule: str = "2R",
    config: RiskConfig | None = None,
    trail: bool = True,
    entry: str = "limit",
) -> pd.DataFrame:
    """One row per event: the signal, the realised move, whether the call was right,
    and the long trade the bot would have taken.

    `cost_bps` is the round-trip cost charged on every trade taken. 25 bps
    is a rough figure for NSE delivery trades (STT, exchange fees, stamp duty,
    GST and some slippage); tune it to your broker. `stop_rule`, `target_rule`
    and `config` go to the exit planner; see risk/exits.py. `entry` is the
    entry rule (see simulate_long): "limit", the bot's ticket, or "open".
    """
    config = config or RiskConfig()
    bench = prices.daily(benchmark) if benchmark else None
    rows = []
    for ev in events:
        row = {
            "event_id": ev.event_id,
            "ticker": ev.ticker,
            "published_at": ev.published_at.isoformat(),
            "headline": ev.headline,
        }
        try:
            bars = prices.daily(ev.ticker)
        except (FileNotFoundError, LookupError) as e:
            rows.append({**row, "status": f"no_prices: {e}"})
            continue

        try:
            signal = engine.simulate(ev)
        except MissingViews as e:
            rows.append({**row, "status": f"no_signal: {e}"})
            continue
        row.update(
            sentiment=signal.sentiment.value,
            confidence=signal.confidence,
            score=signal.score,
            time_horizon=signal.time_horizon,
            rationale=signal.rationale,
        )

        sessions = sessions_for(signal)
        out = measure(bars, ev, sessions, bench)
        if out is None:
            rows.append({**row, "status": "insufficient_bars"})
            continue

        direction = signal.sentiment.direction
        excess = out.ret - out.bench_ret if out.bench_ret is not None else None
        row.update(
            status="ok",
            entry_date=out.entry_date.date().isoformat(),
            exit_date=out.exit_date.date().isoformat(),
            entry_price=out.entry_price,
            exit_price=out.exit_price,
            ret=out.ret,
            bench_ret=out.bench_ret,
            excess_ret=excess,
            max_up=out.max_up,
            max_down=out.max_down,
            hit=(None if direction == 0 else bool(direction * out.ret > 0)),
        )
        row.update(_trade_columns(signal, bars, entry_index(bars, ev), sessions, cost_bps,
                                  stop_rule, target_rule, config, trail, entry))
        rows.append(row)
    return pd.DataFrame(rows)


def _trade_columns(signal: Signal, bars: pd.DataFrame, i: int, sessions: int, cost_bps: float,
                   stop_rule: str, target_rule: str, config: RiskConfig, trail: bool,
                   entry: str = "limit") -> dict:
    """The trade fields of a result row. No trade means trade_ret 0.0: the money stayed in cash."""
    if signal.sentiment is not Sentiment.BULLISH:
        why = "bearish: long-only, no short" if signal.sentiment is Sentiment.BEARISH else "neutral"
        return {"traded": False, "no_trade_reason": why, "trade_ret": 0.0}
    try:
        t = simulate_long(bars, i, sessions, signal.ticker, stop_rule, target_rule, config, trail, entry)
    except PlanRefused as e:
        return {"traded": False, "no_trade_reason": f"plan refused: {e}", "trade_ret": 0.0}
    except NotFilled as e:
        return {"traded": False, "no_trade_reason": f"limit not filled: {e}", "trade_ret": 0.0}
    return {
        "traded": True,
        "no_trade_reason": None,
        "trade_entry": t.entry,
        "trade_stop": t.stop,
        "trade_target": t.target,
        "trade_exit_date": t.exit_date.date().isoformat(),
        "trade_exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "trade_r": t.r_multiple,
        "trade_ret": t.ret - cost_bps / 1e4,
    }


__all__ = ["run_backtest", "measure", "entry_index", "simulate_long", "NotFilled", "Outcome", "LongTrade",
           "Sentiment"]
