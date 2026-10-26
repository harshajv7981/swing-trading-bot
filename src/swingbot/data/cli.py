"""swingbot-data: print one kind of market data for a ticker, as of a time.

This is the tool Claude Code calls while researching a stock (the
market-research skill). Every answer is cut to --as-of: nothing that closed or
was published later is shown. Missing data comes back as one sentence that says
why, never as an empty result.

    swingbot-data technicals RELIANCE
    swingbot-data news TCS --as-of 2026-08-03 --days 5
    swingbot-data all INFY --as-of "2026-08-03 11:00"
"""

from __future__ import annotations

import argparse

from .router import CATEGORIES, DataConfig, Router, parse_as_of


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-data", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("category", choices=[*CATEGORIES, "all"])
    p.add_argument("ticker", help="NSE symbol, e.g. RELIANCE (no .NS)")
    p.add_argument("--as-of", help="YYYY-MM-DD (end of that day) or 'YYYY-MM-DD HH:MM' IST; default now")
    p.add_argument("--days", type=int, default=7, help="look-back window for news and filings")
    p.add_argument("--rows", type=int, default=30, help="sessions to print for prices")
    p.add_argument("--config", help="data sources TOML (default config/data_sources.toml)")
    p.add_argument("--source", action="append", default=[], metavar="CATEGORY=CHAIN",
                   help="override one chain, e.g. --source prices=yfinance")
    args = p.parse_args(argv)

    config = DataConfig.load(args.config)
    for item in args.source:
        category, sep, chain = item.partition("=")
        if not sep:
            p.error(f"--source takes CATEGORY=CHAIN, got {item!r}")
        config = config.with_sources(**{category: chain})
    router = Router(config)
    as_of = parse_as_of(args.as_of)

    for category in CATEGORIES if args.category == "all" else [args.category]:
        print(router.text(category, args.ticker, as_of, days=args.days, rows=args.rows))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
