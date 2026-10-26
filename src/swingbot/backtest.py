"""Replay news events through a swarm and measure what prices did next.

Timing rules (the part that most often makes a backtest lie):

* NSE cash market opens 09:15 IST. News published before the open on a trading
  day can be acted on at that day's open. Anything later enters at the *next*
  trading day's open. We never enter at a price printed before the news.
* The exit is the close of the Nth trading session, counting the entry session
  as session 1, where N = ceil(horizon_hours / 24). 72h -> 3 sessions.
* The swarm only receives the NewsEvent. It never sees prices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import time

import pandas as pd

from .models import IST, NewsEvent, Sentiment, Signal
from .prices import PriceSource
from .swarm.base import SwarmEngine

MARKET_OPEN = time(9, 15)


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
    if same_day and ts.time() >= MARKET_OPEN:
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


def sessions_for(signal: Signal) -> int:
    return max(1, math.ceil(signal.horizon_hours / 24))


def run_backtest(
    events: list[NewsEvent],
    engine: SwarmEngine,
    prices: PriceSource,
    benchmark: str | None = None,
    cost_bps: float = 25.0,
) -> pd.DataFrame:
    """One row per event: the signal, the realised move, and whether the call was right.

    `cost_bps` is the round-trip cost charged on every directional call. 25 bps
    is a rough figure for NSE delivery trades (STT, exchange fees, stamp duty,
    GST and some slippage); tune it to your broker.
    """
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

        signal = engine.simulate(ev)
        row.update(
            sentiment=signal.sentiment.value,
            confidence=signal.confidence,
            score=signal.score,
            time_horizon=signal.time_horizon,
            rationale=signal.rationale,
        )

        out = measure(bars, ev, sessions_for(signal), bench)
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
            trade_ret=(0.0 if direction == 0 else direction * out.ret - cost_bps / 1e4),
        )
        rows.append(row)
    return pd.DataFrame(rows)


__all__ = ["run_backtest", "measure", "entry_index", "Outcome", "Sentiment"]
