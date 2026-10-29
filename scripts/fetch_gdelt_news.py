"""Collect Indian business headlines from GDELT into a backtest events file.

    python scripts/fetch_gdelt_news.py --universe data/universe/nifty100.csv data/universe/midcap150.csv \
        --start 2026-07-01 --end 2026-10-15 --out data/raw/news/events.csv

No API key. Uses the GDELT DOC 2.0 API, which officially covers the last three
months, so older chunks may come back empty. Results merge into --out, so the
same command run daily (with --start yesterday) keeps the file growing.

GDELT data is free for any use with a citation: https://www.gdeltproject.org/
Only headlines and links are stored, never article text.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from swingbot.data.remote import GDELT_MAX, gdelt_articles as fetch
from swingbot.news import gdelt_query, gdelt_rows, load_universe, merge_into


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--universe", nargs="+", default=["data/universe/nifty100.csv", "data/universe/midcap150.csv"],
                   help="symbol,name,aliases CSV(s); a symbol in two is read once")
    p.add_argument("--tickers", nargs="*", help="only these symbols from the universe")
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument("--chunk-days", type=int, default=7, help="days per request; GDELT caps a request at 250 articles")
    p.add_argument("--pause", type=float, default=5.0, help="seconds between requests (GDELT asks for one every 5s)")
    p.add_argument("--out", default="data/raw/news/events.csv")
    args = p.parse_args()

    companies = load_universe(args.universe)
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        companies = [c for c in companies if c.symbol in wanted]

    for c in companies:
        rows, capped = [], 0
        day = args.start
        while day <= args.end:
            chunk_end = min(day + timedelta(days=args.chunk_days - 1), args.end)
            try:
                articles = fetch(gdelt_query(c), day, chunk_end)
            except Exception as e:  # keep going; report which chunks failed
                print(f"FAIL {c.symbol} {day}..{chunk_end}: {e}")
                articles = []
            capped += len(articles) >= GDELT_MAX
            rows += gdelt_rows(articles, c)
            day = chunk_end + timedelta(days=1)
            time.sleep(args.pause)
        added, total = merge_into(args.out, rows)
        note = f"  ({capped} chunks hit the 250 cap: use a smaller --chunk-days)" if capped else ""
        print(f"ok   {c.symbol}: {len(rows)} headlines, {added} new, file now {total}{note}")

    print(f"\nWrote {args.out}. Source: The GDELT Project, https://www.gdeltproject.org/")


if __name__ == "__main__":
    main()
