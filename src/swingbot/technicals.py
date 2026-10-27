"""Technical snapshot for a news event, built only from bars known at publication.

The idea comes from TradingAgents' market analyst (Apache 2.0,
github.com/TauricResearch/TradingAgents): one deterministic fact sheet of
indicators that the model must quote instead of inventing price levels, and a
date clamp so it can never see a bar from after the trade date. The code here is
our own, in plain pandas, so it needs no extra dependency.

Which bars count as known: every session before the publication date, plus the
publication date itself only when the news came out after the 15:30 IST close.
News at 11:00 cannot know that day's close, even though the backtest enters at
the next open.
"""

from __future__ import annotations

from datetime import time

import pandas as pd

from .models import IST, NewsEvent
from .prices import PriceSource

MARKET_CLOSE = time(15, 30)

# Saved prices are adjusted for splits, bonuses and dividends paid since, so on
# a past date a rupee level here can differ from the price quoted in that day's
# news. Percent changes and indicator positions are unaffected.
ADJUSTED_NOTE = ("- Prices are adjusted for later splits, bonuses and dividends: a rupee level may not "
                 "match the price quoted in the news; compare percentages, not levels.")


def known_bars(bars: pd.DataFrame, event: NewsEvent) -> pd.DataFrame:
    """The bars a trader could have seen when `event` was published."""
    return bars_known_at(bars, event.published_at)


def bars_known_at(bars: pd.DataFrame, when) -> pd.DataFrame:
    """The bars whose close had printed by `when` (a timezone-aware time)."""
    ts = pd.Timestamp(when).tz_convert(IST)
    day = ts.tz_localize(None).normalize()
    if ts.time() >= MARKET_CLOSE:
        return bars[bars.index <= day]
    return bars[bars.index < day]


def _wilder(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _last(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def indicators(bars: pd.DataFrame) -> dict[str, float | None]:
    """Latest value of each indicator; None where history is too short."""
    close, high, low, vol = bars["close"], bars["high"], bars["low"], bars["volume"]

    delta = close.diff()
    gain, loss = _wilder(delta.clip(lower=0), 14), _wilder(-delta.clip(upper=0), 14)
    rsi = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    rsi = rsi.where(loss != 0, 100.0).where(gain.notna())

    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = (ema12 - ema26).where(close.expanding().count() >= 26)
    macd_signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()

    mid = close.rolling(20).mean()
    sd = close.rolling(20).std(ddof=0)
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)

    def ret(n: int) -> float | None:
        return float(close.iloc[-1] / close.iloc[-1 - n] - 1) if len(close) > n else None

    return {
        "close": _last(close),
        "ret_1d": ret(1),
        "ret_5d": ret(5),
        "ret_20d": ret(20),
        "ema_10": _last(close.ewm(span=10, adjust=False, min_periods=10).mean()),
        "sma_50": _last(close.rolling(50).mean()),
        "sma_200": _last(close.rolling(200).mean()),
        "rsi_14": _last(rsi),
        "macd": _last(macd),
        "macd_signal": _last(macd_signal),
        "macd_hist": _last(macd - macd_signal),
        "boll_mid": _last(mid),
        "boll_upper": _last(mid + 2 * sd),
        "boll_lower": _last(mid - 2 * sd),
        "atr_14": _last(_wilder(true_range, 14)),
        "vwma_20": _last((close * vol).rolling(20).sum() / vol.rolling(20).sum()),
        "high_20": _last(high.rolling(20).max()),
        "low_20": _last(low.rolling(20).min()),
        "volume_vs_20d": _last(vol / vol.rolling(20).mean()),
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def _vs(close: float, level: float | None) -> str:
    return "n/a" if level is None else f"{_num(level)} (close {_pct(close / level - 1)} vs it)"


def snapshot(prices: PriceSource, event: NewsEvent) -> str:
    """Markdown fact sheet for the brief, or a line saying why there is none.

    "Unavailable" is never written as if the chart were flat: a missing file or
    too little history is said plainly so nobody reads it as a neutral signal.
    """
    try:
        bars = known_bars(prices.daily(event.ticker), event)
    except (FileNotFoundError, LookupError) as e:
        return f"Technicals: unavailable ({e})."
    return describe(bars)


def describe(bars: pd.DataFrame) -> str:
    """The fact sheet for bars already cut to what was known (see `bars_known_at`)."""
    if len(bars) < 20:
        return f"Technicals: unavailable (only {len(bars)} sessions of history before the news)."

    v = indicators(bars)
    c = v["close"]
    atr_pct = None if v["atr_14"] is None else v["atr_14"] / c
    boll_pos = None
    if v["boll_upper"] is not None and v["boll_upper"] != v["boll_lower"]:
        boll_pos = (c - v["boll_lower"]) / (v["boll_upper"] - v["boll_lower"])
    return "\n".join([
        f"Technicals as of the {bars.index[-1]:%Y-%m-%d} close (the last session that had closed by then):",
        f"- Last close {_num(c)}; change 1d {_pct(v['ret_1d'])}, 5d {_pct(v['ret_5d'])}, 20d {_pct(v['ret_20d'])}",
        f"- Trend: EMA10 {_vs(c, v['ema_10'])}; SMA50 {_vs(c, v['sma_50'])}; SMA200 {_vs(c, v['sma_200'])}",
        f"- Momentum: RSI14 {_num(v['rsi_14'], 1)}; MACD {_num(v['macd'])} vs signal {_num(v['macd_signal'])}"
        f" (histogram {_num(v['macd_hist'])})",
        f"- Volatility: ATR14 {_num(v['atr_14'])} ({'n/a' if atr_pct is None else f'{atr_pct:.1%}'} of close);"
        f" Bollinger {_num(v['boll_lower'])} to {_num(v['boll_upper'])}"
        f" (close at {'n/a' if boll_pos is None else f'{boll_pos:.0%}'} of the band)",
        f"- Range and volume: 20-day range {_num(v['low_20'])} to {_num(v['high_20'])};"
        f" VWMA20 {_num(v['vwma_20'])}; last volume {_num(v['volume_vs_20d'], 1)}x its 20-day average",
        ADJUSTED_NOTE,
    ])
