"""One way in for every piece of market data the swarm reads.

The design follows the data layer of TradingAgents (Apache 2.0,
github.com/TauricResearch/TradingAgents); the code is our own:

* Each source is a small vendor function (see `vendors.py`) that raises one of
  the typed errors below instead of returning an empty frame or prose.
* A config file names the vendor chain per category, tried in order, so moving
  prices from Yahoo to Dhan later is a config change, not a code change.
* Every request carries an as-of time. Nothing published or closed after it is
  served, whatever the caller asks for.
* Failures come back as plain sentences that tell the model what the gap means
  ("unavailable says nothing about the stock; do not estimate values"), because
  a model handed an empty result tends to read it as a neutral signal or to
  invent numbers.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from ..models import IST, NewsEvent
from .. import technicals

CATEGORIES = ("prices", "technicals", "news", "filings", "fundamentals")
DEFAULT_CONFIG = Path("config/data_sources.toml")


class DataError(Exception):
    """Base for the errors a vendor may raise."""


class NoData(DataError):
    """The vendor answered and has nothing usable for this ticker and date."""


class VendorUnavailable(DataError):
    """The vendor could not be reached or refused; it said nothing about the ticker."""


class NotConfigured(DataError):
    """The vendor needs something that is missing: a package, a key, a file."""


class Withheld(DataError):
    """The vendor only serves present-day values, so a past as-of time can't use them."""


@dataclass(frozen=True)
class DataConfig:
    sources: dict[str, str] = field(default_factory=lambda: {
        "prices": "csv,yfinance",
        "news": "events",
        "filings": "events",
        "fundamentals": "nse_results,yfinance",
    })
    prices_dir: Path = Path("data/raw/prices")
    events_file: Path = Path("data/raw/news/events.csv")
    cache_prices: bool = True  # write Yahoo downloads into prices_dir for the csv vendor

    @classmethod
    def load(cls, path: str | Path | None = None) -> "DataConfig":
        """Read a TOML config; with no path, the repo default if present, else built-in defaults."""
        path = Path(path) if path else DEFAULT_CONFIG
        if not path.exists():
            if path != DEFAULT_CONFIG:
                raise FileNotFoundError(f"no data config at {path}")
            return cls()
        raw = tomllib.loads(path.read_text())
        base = cls()
        paths, options = raw.get("paths", {}), raw.get("options", {})
        return cls(
            sources={**base.sources, **raw.get("sources", {})},
            prices_dir=Path(paths.get("prices_dir", base.prices_dir)),
            events_file=Path(paths.get("events_file", base.events_file)),
            cache_prices=bool(options.get("cache_prices", base.cache_prices)),
        )

    def with_sources(self, **chains: str) -> "DataConfig":
        return replace(self, sources={**self.sources, **chains})


def parse_as_of(value: str | None) -> pd.Timestamp:
    """`None` is now; a bare date is the end of that day; times without an offset are IST."""
    if not value:
        return pd.Timestamp(datetime.now(IST))
    ts = pd.Timestamp(value)
    if len(value.strip()) == 10:  # YYYY-MM-DD
        ts = ts + pd.Timedelta(hours=23, minutes=59, seconds=59)
    return ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)


def _stamp(ts: pd.Timestamp) -> str:
    return f"{ts.tz_convert(IST):%Y-%m-%d %H:%M} IST"


class Router:
    def __init__(self, config: DataConfig | None = None):
        from . import vendors

        self.config = config or DataConfig.load()
        self.vendors = vendors.REGISTRY
        self.downloaders = vendors.DOWNLOADERS
        self.context = vendors.VendorContext(self.config)

    # --- routing -----------------------------------------------------------

    def chain(self, category: str) -> list[str]:
        key = "prices" if category == "technicals" else category
        if key not in self.vendors:
            raise ValueError(f"unknown data category {category!r} (choose from {', '.join(CATEGORIES)})")
        names = [v.strip() for v in self.config.sources.get(key, "").split(",") if v.strip()]
        unknown = [v for v in names if v not in self.vendors[key]]
        if not names or unknown:
            raise ValueError(
                f"data config for {key!r} names {unknown or 'no vendor'}; "
                f"available: {', '.join(self.vendors[key])}"
            )
        return names

    def fetch(self, category: str, ticker: str, as_of: pd.Timestamp):
        """(vendor name, raw result) from the first vendor in the chain that serves it.

        Raises the most informative error when none does: an unreachable vendor
        outranks "no data", because it never said whether it had the ticker.
        """
        key = "prices" if category == "technicals" else category
        as_of = pd.Timestamp(as_of).tz_convert(IST)
        errors: list[tuple[str, DataError]] = []
        for name in self.chain(category):
            try:
                return name, self.vendors[key][name](ticker.upper(), as_of, self.context)
            except DataError as e:
                errors.append((name, e))
        for kind in (VendorUnavailable, Withheld, NoData, NotConfigured):
            hits = [(n, e) for n, e in errors if isinstance(e, kind)]
            if hits:
                detail = "; ".join(f"{n}: {e}" for n, e in errors)
                raise kind(detail) from hits[0][1]
        raise AssertionError("unreachable")

    def download(self, category: str, ticker: str, start, end):
        """(vendor name, bars for `start`..`end`) from the first vendor in the chain that can
        download a range. Used by the daily refresh, which then saves what it gets."""
        names = [n for n in self.chain(category) if n in self.downloaders.get(category, {})]
        if not names:
            raise NotConfigured(f"no source in the {category!r} chain can download "
                                f"({', '.join(self.chain(category))}); add one such as yfinance")
        errors: list[tuple[str, DataError]] = []
        for name in names:
            try:
                return name, self.downloaders[category][name](ticker.upper(), start, end)
            except DataError as e:
                errors.append((name, e))
        detail = "; ".join(f"{n}: {e}" for n, e in errors)
        for kind in (VendorUnavailable, NoData, NotConfigured):
            if any(isinstance(e, kind) for _, e in errors):
                raise kind(detail)
        raise errors[0][1]

    # --- text for a model ----------------------------------------------------

    def text(self, category: str, ticker: str, as_of: pd.Timestamp, *, days: int = 7, rows: int = 30,
             exclude_event: str | None = None) -> str:
        """Always a string: the data, or one sentence saying exactly what is missing and why."""
        ticker, as_of = ticker.upper(), pd.Timestamp(as_of).tz_convert(IST)
        try:
            vendor, raw = self.fetch(category, ticker, as_of)
            return getattr(self, f"_format_{category}")(ticker, as_of, vendor, raw, days=days, rows=rows,
                                                        exclude_event=exclude_event)
        except VendorUnavailable as e:
            return (f"DATA_UNAVAILABLE: no source could serve {category} for {ticker} right now ({e}). "
                    "This says nothing about the stock. Report it as unavailable; do not estimate values.")
        except Withheld as e:
            return (f"WITHHELD: {category} for {ticker} as of {_stamp(as_of)} ({e}). No point-in-time "
                    "source had it, and present-day values would leak later information into a past date.")
        except NoData as e:
            return (f"NO_DATA: no {category} for {ticker} up to {_stamp(as_of)} ({e}). "
                    "Report it as missing; do not estimate values.")
        except NotConfigured as e:
            return f"NOT_CONFIGURED: {category} for {ticker} needs setup ({e})."

    def _format_prices(self, ticker, as_of, vendor, bars, *, rows, **_):
        known = technicals.bars_known_at(bars, as_of)
        if known.empty:
            raise NoData(f"{vendor}: no session closed by then")
        tail = known.tail(rows).round(2)
        return (f"# Daily bars for {ticker}: last {len(tail)} sessions to the {known.index[-1]:%Y-%m-%d} "
                f"close (source: {vendor})\n{technicals.ADJUSTED_NOTE[2:]}\n" + tail.to_csv())

    def _format_technicals(self, ticker, as_of, vendor, bars, **_):
        return f"{technicals.describe(technicals.bars_known_at(bars, as_of))}\n(source: {vendor})"

    def _items(self, items: list[NewsEvent], as_of, days, exclude_event):
        start = as_of - timedelta(days=days)
        return [e for e in items if start <= e.published_at <= as_of and e.event_id != exclude_event]

    def _format_news(self, ticker, as_of, vendor, items, *, days, exclude_event, **_):
        return self._list("News", ticker, as_of, vendor, self._items(items, as_of, days, exclude_event), days)

    def _format_filings(self, ticker, as_of, vendor, items, *, days, exclude_event, **_):
        return self._list("Exchange filings", ticker, as_of, vendor,
                          self._items(items, as_of, days, exclude_event), days, body=True)

    def _format_fundamentals(self, ticker, as_of, vendor, raw, *, exclude_event, **_):
        if isinstance(raw, dict):
            lines = [f"- {k}: {v}" for k, v in raw.items() if v is not None]
            return f"# Fundamentals for {ticker}, present-day values (source: {vendor})\n" + "\n".join(lines)
        items = self._items(raw, as_of, 120, exclude_event)
        return self._list("Results filings", ticker, as_of, vendor, items, 120, body=True)

    def _list(self, title, ticker, as_of, vendor, items, days, body=False):
        start = as_of - timedelta(days=days)
        head = f"# {title} for {ticker}, {_stamp(start)} to {_stamp(as_of)} (source: {vendor})"
        if not items:
            return (f"{head}\nNone found. The source only holds what was collected for it, "
                    "so this is not proof that nothing happened.")
        lines = []
        for e in items:
            line = f"- {_stamp(pd.Timestamp(e.published_at))} · {e.headline}"
            if body and e.body:
                line += f"\n  {e.body[:300]}"
            lines.append(line)
        return head + "\n" + "\n".join(lines)

