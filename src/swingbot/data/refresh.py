"""swingbot-refresh: bring saved prices and news up to date, once a day.

    swingbot-refresh                      # universe + mood series prices, GDELT news
    swingbot-refresh --accept-nse-terms   # also NSE filings (read the note below)
    swingbot-refresh --only prices --tickers TCS INFY

Run it on your own machine after the close (16:00 IST or later). The cloud
workspace's network blocks Yahoo, GDELT and NSE.

Everything goes where the data router reads it (config/data_sources.toml), so
`swingbot-data`, the briefs and the market mood see the new rows straight away:

* Prices: for each stock in the universe, plus the benchmark and the mood
  series (NIFTY50, NIFTYFMCG, GOLDBEES, USDINR, INDIAVIX), the bars since the
  last saved one come from the first downloading source in the prices chain
  (Yahoo today) and are added to `<prices_dir>/<TICKER>.csv`. A few saved days
  are fetched again each time; if Yahoo's adjusted closes for those days have
  moved (a split, bonus or dividend since the last run), the whole file is
  downloaded again, because adjusted and unadjusted bars can't be mixed. A bar
  for a session that hasn't closed yet is never saved.
* News: GDELT headlines since the last successful run for each stock (one day
  of overlap, since GDELT can see an article late), merged into the events file.
* Filings: NSE announcements the same way, only with --accept-nse-terms (or
  `accept_nse_terms = true` under [refresh] in the config). NSE's terms forbid
  automated collection and simulation use; see `swingbot.data.remote`.

It is safe to run twice: a stock already up to date is skipped without a
request, and rows are merged by id, so nothing is duplicated. A lock file stops
two runs overlapping. One stock failing doesn't stop the rest; the exit code is
1 if anything failed, and the next run picks up where that one stopped.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

from .. import news
from ..models import IST
from ..prices import CSVPriceSource
from ..regime import SERIES as MOOD_SERIES
from ..risk.calendar import NSECalendar
from ..technicals import MARKET_CLOSE, bars_known_at
from .router import DEFAULT_CONFIG, DataConfig, DataError, Router, parse_as_of
from .vendors import write_bars

BENCHMARK = "NIFTY50"
OVERLAP_DAYS = 7          # saved calendar days fetched again to spot a changed adjustment
ADJUST_TOLERANCE = 0.001  # overlap closes further apart than 0.1% mean Yahoo re-adjusted
HISTORY_DAYS = 450        # first download for a new ticker: enough for the 200-day trend
NEWS_FIRST_DAYS = 7       # first run for a ticker looks back this far
NEWS_MAX_DAYS = 85        # GDELT's DOC API only promises about three months
GDELT_CHUNK_DAYS = 7
NSE_CHUNK_DAYS = 31


@dataclass(frozen=True)
class RefreshSettings:
    universe: Path = Path("data/universe/large_caps.csv")
    state_file: Path = Path("data/raw/refresh_state.json")
    accept_nse_terms: bool = False
    gdelt_pause: float = 5.0  # GDELT asks for one request every 5 seconds
    nse_pause: float = 4.0

    @classmethod
    def load(cls, path: str | Path | None = None) -> "RefreshSettings":
        path = Path(path) if path else DEFAULT_CONFIG
        raw = tomllib.loads(path.read_text()).get("refresh", {}) if path.exists() else {}
        base = cls()
        return cls(
            universe=Path(raw.get("universe", base.universe)),
            state_file=Path(raw.get("state_file", base.state_file)),
            accept_nse_terms=bool(raw.get("accept_nse_terms", base.accept_nse_terms)),
            gdelt_pause=float(raw.get("gdelt_pause", base.gdelt_pause)),
            nse_pause=float(raw.get("nse_pause", base.nse_pause)),
        )


@dataclass
class Item:
    kind: str       # prices, news, filings
    ticker: str
    status: str     # updated, rebuilt, current, no_new, skipped, failed
    detail: str = ""
    added: int = 0


@dataclass
class RefreshReport:
    started: str
    items: list[Item] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.status == "failed" for i in self.items)

    def failed(self) -> list[Item]:
        return [i for i in self.items if i.status == "failed"]

    def to_json(self) -> dict:
        return {"started": self.started, "ok": self.ok, "items": [asdict(i) for i in self.items]}

    def text(self) -> str:
        lines = []
        for kind in ("prices", "news", "filings"):
            items = [i for i in self.items if i.kind == kind]
            if not items:
                continue
            counts: dict[str, int] = {}
            for i in items:
                counts[i.status] = counts.get(i.status, 0) + 1
            added = sum(i.added for i in items)
            summary = ", ".join(f"{n} {s}" for s, n in counts.items())
            lines.append(f"{kind}: {summary}; {added} new rows")
            lines += [f"  FAIL {i.ticker}: {i.detail}" for i in items if i.status == "failed"]
            lines += [f"  {i.status} {i.ticker}: {i.detail}" for i in items
                      if i.status in ("rebuilt", "skipped") and i.detail]
        return "\n".join(lines) or "nothing to refresh"


# --- prices ------------------------------------------------------------------------

def last_closed_session(now: pd.Timestamp, calendar: NSECalendar) -> date:
    """The latest NSE session whose close had printed by `now`."""
    now = pd.Timestamp(now).tz_convert(IST)
    day = now.date()
    if not (calendar.is_session(day) and now.time() >= MARKET_CLOSE):
        day -= timedelta(days=1)
        while not calendar.is_session(day):
            day -= timedelta(days=1)
    return day


def _saved(prices_dir: Path, ticker: str) -> pd.DataFrame | None:
    try:
        return CSVPriceSource(prices_dir).daily(ticker)
    except FileNotFoundError:
        return None


def refresh_prices(router: Router, tickers: list[str], now: pd.Timestamp,
                   calendar: NSECalendar | None = None) -> list[Item]:
    calendar = calendar or NSECalendar.load()
    prices_dir = Path(router.config.prices_dir)
    target = last_closed_session(now, calendar)
    today = pd.Timestamp(now).tz_convert(IST).date()
    items = []
    for t in tickers:
        saved = _saved(prices_dir, t)
        last = saved.index[-1].date() if saved is not None and not saved.empty else None
        if last is not None and last >= target:
            items.append(Item("prices", t, "current", f"last bar {last}"))
            continue
        start = last - timedelta(days=OVERLAP_DAYS) if last else today - timedelta(days=HISTORY_DAYS)
        try:
            vendor, bars = router.download("prices", t, start, today)
            bars = bars_known_at(bars, now)
            if last is not None and _readjusted(saved, bars):
                vendor, full = router.download("prices", t, saved.index[0].date(), today)
                full = bars_known_at(full, now)
                write_bars(prices_dir / f"{t}.csv", full)
                items.append(Item("prices", t, "rebuilt",
                                  f"{vendor} adjusted closes changed (split, bonus or dividend); "
                                  f"downloaded {len(full)} bars again",
                                  int((full.index > saved.index[-1]).sum())))
                continue
        except DataError as e:
            items.append(Item("prices", t, "failed", str(e)))
            continue
        new = bars[bars.index > saved.index[-1]] if last is not None else bars
        if new.empty:
            items.append(Item("prices", t, "no_new", f"{vendor} has nothing after {last} yet"))
            continue
        merged = bars if saved is None else pd.concat([saved[~saved.index.isin(bars.index)], bars]).sort_index()
        write_bars(prices_dir / f"{t}.csv", merged)
        items.append(Item("prices", t, "updated", f"{vendor} to {new.index[-1]:%Y-%m-%d}", len(new)))
    return items


def _readjusted(saved: pd.DataFrame, fresh: pd.DataFrame) -> bool:
    """True when fetched closes disagree with saved ones on the days both have.

    The newest saved bar is left out: a close fetched right after the bell can
    still be revised, and the merge replaces it anyway.
    """
    both = saved.index[:-1].intersection(fresh.index)
    if both.empty:
        return False
    old, new = saved.loc[both, "close"], fresh.loc[both, "close"]
    return bool(((new - old).abs() / old.abs()).max() > ADJUST_TOLERANCE)


# --- news and filings --------------------------------------------------------------

def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _window(state: dict, source: str, ticker: str, today: date) -> tuple[date, date]:
    """From the day before the last successful run (GDELT can see an article late),
    or NEWS_FIRST_DAYS back on a first run, never further than NEWS_MAX_DAYS."""
    done = state.get(source, {}).get(ticker)
    start = date.fromisoformat(done) - timedelta(days=1) if done else today - timedelta(days=NEWS_FIRST_DAYS)
    return max(start, today - timedelta(days=NEWS_MAX_DAYS)), today


def _chunks(start: date, end: date, days: int):
    while start <= end:
        stop = min(start + timedelta(days=days - 1), end)
        yield start, stop
        start = stop + timedelta(days=1)


def refresh_news(router: Router, companies: list[news.Company], now: pd.Timestamp, state_file: Path, *,
                 fetch: Callable | None = None, pause: float = 5.0,
                 sleep: Callable[[float], None] = time.sleep) -> list[Item]:
    from .remote import GDELT_MAX, gdelt_articles

    fetch = fetch or gdelt_articles
    today = pd.Timestamp(now).tz_convert(IST).date()
    state = _load_state(state_file)
    items = []
    for i, c in enumerate(companies):
        start, end = _window(state, "gdelt", c.symbol, today)
        rows, capped = [], 0
        try:
            for a, b in _chunks(start, end, GDELT_CHUNK_DAYS):
                if i or a != start:
                    sleep(pause)
                articles = fetch(news.gdelt_query(c), a, b)
                capped += len(articles) >= GDELT_MAX
                rows += news.gdelt_rows(articles, c)
        except Exception as e:  # network, rate limit, GDELT format change
            items.append(Item("news", c.symbol, "failed", f"GDELT {start}..{end}: {e}"))
            continue
        added, _ = news.merge_into(router.config.events_file, rows)
        state.setdefault("gdelt", {})[c.symbol] = end.isoformat()
        _save_state(state_file, state)
        note = f"; {capped} requests hit GDELT's {GDELT_MAX} cap, so some were cut" if capped else ""
        items.append(Item("news", c.symbol, "updated" if added else "no_new",
                          f"{start}..{end}, {len(rows)} headlines{note}", added))
    return items


def refresh_filings(router: Router, companies: list[news.Company], now: pd.Timestamp, state_file: Path, *,
                    session_factory: Callable | None = None, pause: float = 4.0,
                    sleep: Callable[[float], None] = time.sleep) -> list[Item]:
    from .remote import NSESession

    if not str(router.config.events_file).startswith("data/raw/"):
        print("WARNING: the events file is outside data/raw/, so git will not ignore it. "
              "Do not commit NSE data.", file=sys.stderr)
    try:
        nse = (session_factory or NSESession)()
    except Exception as e:
        return [Item("filings", c.symbol, "failed", f"could not open an NSE session: {e}") for c in companies]
    today = pd.Timestamp(now).tz_convert(IST).date()
    state = _load_state(state_file)
    items = []
    for i, c in enumerate(companies):
        start, end = _window(state, "nse", c.symbol, today)
        raw = []
        try:
            for a, b in _chunks(start, end, NSE_CHUNK_DAYS):
                if i or a != start:
                    sleep(pause)
                raw += nse.announcements(c.symbol, a, b)
        except Exception as e:
            items.append(Item("filings", c.symbol, "failed", f"NSE {start}..{end}: {e}"))
            continue
        rows = news.nse_rows(raw)
        added, _ = news.merge_into(router.config.events_file, rows)
        state.setdefault("nse", {})[c.symbol] = end.isoformat()
        _save_state(state_file, state)
        items.append(Item("filings", c.symbol, "updated" if added else "no_new",
                          f"{start}..{end}, {len(raw)} filings, {len(rows)} kept", added))
    return items


# --- the whole refresh ---------------------------------------------------------------

@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"another refresh is running (lock {path})") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def price_tickers(companies: list[news.Company]) -> list[str]:
    """The universe, then the benchmark and the market-mood series."""
    extra = [BENCHMARK, *MOOD_SERIES.values()]
    names = [c.symbol for c in companies] + extra
    return list(dict.fromkeys(names))


def run(router: Router | None = None, settings: RefreshSettings | None = None, *,
        now: pd.Timestamp | None = None, only: tuple[str, ...] = ("prices", "news", "filings"),
        tickers: list[str] | None = None, accept_nse_terms: bool | None = None,
        calendar: NSECalendar | None = None, gdelt_fetch: Callable | None = None,
        nse_session: Callable | None = None, sleep: Callable[[float], None] = time.sleep) -> RefreshReport:
    """The daily refresh. The daily loop calls this; `main` is the command line around it.

    `tickers` narrows the universe (and, for prices, drops the index series
    unless they are named). Filings run only when the terms are accepted, here
    or in the config; otherwise they are reported as skipped.
    """
    router = router or Router()
    settings = settings or RefreshSettings.load()
    now = pd.Timestamp(now) if now is not None else pd.Timestamp(datetime.now(IST))
    now = now.tz_localize(IST) if now.tzinfo is None else now.tz_convert(IST)
    accept = settings.accept_nse_terms if accept_nse_terms is None else accept_nse_terms

    companies = news.load_universe(settings.universe)
    if tickers:
        wanted = [t.upper() for t in tickers]
        companies = [c for c in companies if c.symbol in wanted]
        symbols = wanted
    else:
        symbols = price_tickers(companies)

    report = RefreshReport(started=now.isoformat())
    with _lock(settings.state_file.with_name("refresh.lock")):
        if "prices" in only:
            report.items += refresh_prices(router, symbols, now, calendar)
        if "news" in only:
            report.items += refresh_news(router, companies, now, settings.state_file,
                                         fetch=gdelt_fetch, pause=settings.gdelt_pause, sleep=sleep)
        if "filings" in only:
            if accept:
                report.items += refresh_filings(router, companies, now, settings.state_file,
                                                session_factory=nse_session, pause=settings.nse_pause,
                                                sleep=sleep)
            else:
                report.items.append(Item("filings", "*", "skipped",
                                         "NSE terms not accepted; pass --accept-nse-terms to fetch filings"))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-refresh", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--only", nargs="+", choices=["prices", "news", "filings"],
                   default=["prices", "news", "filings"])
    p.add_argument("--tickers", nargs="+", help="only these symbols (default: the universe and the mood series)")
    p.add_argument("--universe", help="symbol,name,aliases CSV (default from [refresh] in the config)")
    p.add_argument("--accept-nse-terms", action="store_true",
                   help="also fetch NSE filings; this breaks NSE's terms of use, see swingbot.data.remote")
    p.add_argument("--config", help="data sources TOML (default config/data_sources.toml)")
    p.add_argument("--now", help="pretend it is this IST time, 'YYYY-MM-DD HH:MM' (for re-runs and tests)")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    args = p.parse_args(argv)

    settings = RefreshSettings.load(args.config)
    if args.universe:
        settings = RefreshSettings(**{**asdict(settings), "universe": Path(args.universe)})
    try:
        report = run(Router(DataConfig.load(args.config)), settings,
                     now=parse_as_of(args.now) if args.now else None, only=tuple(args.only),
                     tickers=args.tickers, accept_nse_terms=args.accept_nse_terms or None)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    print(json.dumps(report.to_json(), indent=1) if args.json else report.text())
    if "news" in args.only:
        print("News source: The GDELT Project, https://www.gdeltproject.org/", file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
