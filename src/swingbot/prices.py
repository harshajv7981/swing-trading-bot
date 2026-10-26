"""Daily OHLC price sources.

Every source returns a DataFrame indexed by trading date (tz-naive, IST calendar
days) with columns open, high, low, close, volume. Prices should be adjusted for
splits and bonuses, otherwise a 1:1 bonus looks like a 50% crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]

# Saved names for series that are not NSE stocks, and their Yahoo symbols.
# The market-mood checks (swingbot.regime) read these.
YAHOO_SYMBOLS = {
    "NIFTY50": "^NSEI",
    "NIFTYFMCG": "^CNXFMCG",
    "INDIAVIX": "^INDIAVIX",
    "USDINR": "INR=X",
}


class PriceSource(Protocol):
    def daily(self, ticker: str) -> pd.DataFrame: ...


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"price data missing columns {missing}")
    return df[COLUMNS].astype(float).sort_index()


class CSVPriceSource:
    """Reads `<dir>/<TICKER>.csv` with columns date,open,high,low,close,volume."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self._cache: dict[str, pd.DataFrame] = {}

    def daily(self, ticker: str) -> pd.DataFrame:
        if ticker not in self._cache:
            path = self.directory / f"{ticker}.csv"
            if not path.exists():
                raise FileNotFoundError(f"no price file for {ticker} at {path}")
            df = pd.read_csv(path, index_col="date")
            self._cache[ticker] = _normalise(df)
        return self._cache[ticker]


class YFinancePriceSource:
    """Pulls NSE daily bars from Yahoo Finance (`RELIANCE` -> `RELIANCE.NS`).

    Good enough for a research sandbox. Yahoo data has gaps and occasional bad
    prints, so cache to CSV with scripts/fetch_prices.py and inspect before
    trusting a result.
    """

    def __init__(self, start: str, end: str, suffix: str = ".NS"):
        self.start, self.end, self.suffix = start, end, suffix
        self._cache: dict[str, pd.DataFrame] = {}

    def symbol(self, ticker: str) -> str:
        if ticker in YAHOO_SYMBOLS:
            return YAHOO_SYMBOLS[ticker]
        return ticker if ticker.startswith("^") else f"{ticker}{self.suffix}"

    def daily(self, ticker: str) -> pd.DataFrame:
        if ticker not in self._cache:
            import yfinance as yf  # optional dependency: pip install .[data]

            raw = yf.Ticker(self.symbol(ticker)).history(
                start=self.start, end=self.end, interval="1d", auto_adjust=True
            )
            if raw.empty:
                raise LookupError(f"no Yahoo data for {self.symbol(ticker)}")
            self._cache[ticker] = _normalise(raw)
        return self._cache[ticker]
