"""Vendor functions: one per source, each `(ticker, as_of, ctx) -> raw result`.

A vendor returns everything it has; the router cuts it to the as-of time, so a
vendor that forgets to can't leak the future. On failure a vendor raises one of
the router's typed errors, never returns an empty result.

Add a source by writing a function here and listing it in REGISTRY, then name
it in `config/data_sources.toml`.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..events import load_events
from ..models import IST, NewsEvent
from ..prices import YAHOO_SYMBOLS, CSVPriceSource, _normalise
from .router import DataConfig, NoData, NotConfigured, VendorUnavailable, Withheld

# A price file whose last bar is this many days before the as-of date is stale:
# it was saved earlier and never refreshed, so a later source should be tried.
STALE_DAYS = 10
RESULTS_WORDS = ("financial result", "audited result", "unaudited result", "quarterly result")


class VendorContext:
    """Config plus per-run caches shared by the vendor functions."""

    def __init__(self, config: DataConfig):
        self.config = config
        self._events: list[NewsEvent] | None = None

    def events(self) -> list[NewsEvent]:
        if self._events is None:
            path = self.config.events_file
            if not Path(path).exists():
                raise NoData(f"no events file at {path}; build it with scripts/fetch_gdelt_news.py "
                             "or scripts/fetch_nse_announcements.py")
            self._events = load_events(path)
        return self._events


# --- prices ------------------------------------------------------------------


def prices_csv(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> pd.DataFrame:
    try:
        bars = CSVPriceSource(ctx.config.prices_dir).daily(ticker)
    except FileNotFoundError as e:
        raise NoData(str(e)) from e
    last_needed = as_of.tz_localize(None).normalize() - pd.Timedelta(days=STALE_DAYS)
    if bars.empty or bars.index[-1] < last_needed:
        last = "none" if bars.empty else f"{bars.index[-1]:%Y-%m-%d}"
        raise NoData(f"saved bars are stale (last {last})")
    return bars


def prices_yfinance(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> pd.DataFrame:
    day = as_of.tz_convert(IST).tz_localize(None).normalize()
    bars = download_yfinance(ticker, (day - pd.Timedelta(days=450)).date(), day.date())
    if ctx.config.cache_prices:
        _merge_into_csv(Path(ctx.config.prices_dir) / f"{ticker}.csv", bars)
    return bars


def yahoo_symbol(ticker: str) -> str:
    return YAHOO_SYMBOLS.get(ticker) or (ticker if ticker.startswith("^") else f"{ticker}.NS")


def download_yfinance(ticker: str, start, end) -> pd.DataFrame:
    """Adjusted daily bars from Yahoo for the days `start`..`end`, both included."""
    try:
        import yfinance as yf
    except ImportError as e:
        raise NotConfigured("yfinance is not installed; pip install -e '.[data]'") from e
    symbol = yahoo_symbol(ticker)
    try:
        raw = yf.Ticker(symbol).history(start=f"{pd.Timestamp(start):%Y-%m-%d}",
                                        end=f"{pd.Timestamp(end) + pd.Timedelta(days=1):%Y-%m-%d}",
                                        interval="1d", auto_adjust=True)
    except Exception as e:  # network, rate limit, Yahoo format change
        raise VendorUnavailable(f"Yahoo request failed: {e}") from e
    if raw is None or raw.empty:
        raise NoData(f"Yahoo has no bars for {symbol} from {start} to {end}")
    return _normalise(raw)


def _merge_into_csv(path: Path, bars: pd.DataFrame) -> None:
    """Add downloaded bars to the saved file (new rows win), written whole via a temp file."""
    if path.exists():
        old = CSVPriceSource(path.parent).daily(path.stem)
        bars = pd.concat([old[~old.index.isin(bars.index)], bars]).sort_index()
    write_bars(path, bars)


def write_bars(path: Path, bars: pd.DataFrame) -> None:
    """Replace the saved file in one step, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    bars.to_csv(tmp, index_label="date")
    os.replace(tmp, path)


def prices_dhan(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> pd.DataFrame:
    raise NotConfigured("the Dhan data adapter is not built yet; it needs Dhan's paid data plan")


# --- news and filings ----------------------------------------------------------

def _is_filing(e: NewsEvent) -> bool:
    return e.source.startswith("nse")


def news_events(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> list[NewsEvent]:
    return [e for e in ctx.events() if e.ticker == ticker and not _is_filing(e)]


def filings_events(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> list[NewsEvent]:
    return [e for e in ctx.events() if e.ticker == ticker and _is_filing(e)]


# --- fundamentals ------------------------------------------------------------------

def fundamentals_nse_results(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> list[NewsEvent]:
    """Quarterly results filings: point in time, because each carries the exchange timestamp."""
    hits = [e for e in filings_events(ticker, as_of, ctx)
            if e.published_at <= as_of and any(w in e.headline.lower() for w in RESULTS_WORDS)]
    if not hits:
        raise NoData("no results filings collected for this ticker before then")
    return hits


YAHOO_FIELDS = {
    "longName": "Name", "sector": "Sector", "industry": "Industry", "marketCap": "Market cap (Rs)",
    "trailingPE": "P/E (trailing)", "priceToBook": "Price to book", "returnOnEquity": "Return on equity",
    "debtToEquity": "Debt to equity", "revenueGrowth": "Revenue growth (yoy)",
    "earningsGrowth": "Earnings growth (yoy)", "dividendYield": "Dividend yield",
    "fiftyTwoWeekLow": "52-week low", "fiftyTwoWeekHigh": "52-week high",
}


def fundamentals_yfinance(ticker: str, as_of: pd.Timestamp, ctx: VendorContext) -> dict:
    if as_of.tz_convert(IST).date() < datetime.now(IST).date():
        raise Withheld("Yahoo serves only today's profile and ratios, with no history")
    try:
        import yfinance as yf
    except ImportError as e:
        raise NotConfigured("yfinance is not installed; pip install -e '.[data]'") from e
    try:
        info = yf.Ticker(f"{ticker}.NS").info or {}
    except Exception as e:
        raise VendorUnavailable(f"Yahoo request failed: {e}") from e
    out = {label: info.get(key) for key, label in YAHOO_FIELDS.items()}
    if all(v is None for v in out.values()):
        raise NoData(f"Yahoo has no profile for {ticker}.NS")
    return out


REGISTRY = {
    "prices": {"csv": prices_csv, "yfinance": prices_yfinance, "dhan": prices_dhan},
    "news": {"events": news_events},
    "filings": {"events": filings_events},
    "fundamentals": {"nse_results": fundamentals_nse_results, "yfinance": fundamentals_yfinance},
}

# Sources that can download a date range for the daily refresh: `(ticker, start, end) -> bars`.
# The refresh walks the prices chain and uses the first one listed here ("csv"
# is what it writes to, so it is skipped).
DOWNLOADERS = {
    "prices": {"yfinance": download_yfinance},
}
