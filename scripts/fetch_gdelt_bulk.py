"""Collect Indian business headlines from GDELT's bulk files into a backtest events file.

    python scripts/fetch_gdelt_bulk.py --universe data/universe/nifty100.csv \
        --start 2026-07-01 --end 2026-10-27 --out data/raw/news/events.csv

Use this when the DOC API (`fetch_gdelt_news.py`) is rate limiting you, or for
windows older than its three months. It reads GDELT's Global Knowledge Graph
files, one per 15 minutes (96 a day), which have no request limit. Each file
holds all the world's news GDELT saw in that batch, so the download is large,
roughly 0.5 to 1 GB a day, but nothing big is kept: each file is read in
memory, the articles whose title names a company are kept, and the rest is
dropped.

Rows merge into --out after each day, and finished days are listed in
`<out>.gkg_days.json`, so a stopped run picks up where it left off. The rows
have the same ids as the DOC API fetcher's, so running both never doubles a story.

GDELT data is free for any use with a citation: https://www.gdeltproject.org/
Only headlines and links are stored, never article text.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from swingbot.data.remote import gkg_lines
from swingbot.news import HeadlineMatcher, gkg_rows, load_universe, merge_into


def batches(day: date) -> list[datetime]:
    """The 96 GKG batch times for a UTC day: 00:15 to 24:00, each file covering the 15 minutes before it."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [start + timedelta(minutes=15 * i) for i in range(1, 97)]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--universe", default="data/universe/nifty100.csv")
    p.add_argument("--tickers", nargs="*", help="only these symbols from the universe")
    p.add_argument("--start", required=True, type=date.fromisoformat, help="first UTC day")
    p.add_argument("--end", required=True, type=date.fromisoformat, help="last UTC day")
    p.add_argument("--workers", type=int, default=4, help="files downloaded at once")
    p.add_argument("--any-source", action="store_true",
                   help="keep matching titles from any outlet, not only Indian ones")
    p.add_argument("--out", default="data/raw/news/events.csv")
    args = p.parse_args()

    companies = load_universe(args.universe)
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        companies = [c for c in companies if c.symbol in wanted]
    matcher = HeadlineMatcher(companies)

    state = Path(f"{args.out}.gkg_days.json")
    done = set(json.loads(state.read_text())) if state.exists() else set()
    days = [args.start + timedelta(days=i) for i in range((args.end - args.start).days + 1)]
    todo = [d for d in days if d.isoformat() not in done]
    if len(todo) < len(days):
        print(f"{len(days) - len(todo)} day(s) already done; {len(todo)} to go")

    def one(at: datetime):
        got = gkg_lines(at)
        if got is None:
            return at, None, 0
        lines, size = got
        return at, gkg_rows(lines, matcher, args.any_source), size

    began, total_bytes = time.monotonic(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for n, day in enumerate(todo, 1):
            t0, rows, size, missing, failed = time.monotonic(), [], 0, 0, []
            futures = [pool.submit(one, at) for at in batches(day)]
            for f in futures:
                try:
                    _, got, nbytes = f.result()
                except Exception as e:  # noqa: BLE001  keep going; the day stays unfinished and is retried next run
                    failed.append(str(e))
                    continue
                if got is None:
                    missing += 1
                    continue
                rows += got
                size += nbytes
            added, total = merge_into(args.out, rows)
            total_bytes += size
            if not failed:
                done.add(day.isoformat())
                state.write_text(json.dumps(sorted(done), indent=0) + "\n")
            left = (time.monotonic() - began) / n * (len(todo) - n)
            print(f"{'ok  ' if not failed else 'FAIL'} {day}: {len(rows)} headlines, {added} new, file now {total}; "
                  f"{size / 1e6:.0f} MB in {time.monotonic() - t0:.0f}s"
                  + (f"; {missing} batch(es) missing at GDELT" if missing else "")
                  + (f"; {len(failed)} file(s) failed ({failed[0][:120]}), rerun to retry this day" if failed else "")
                  + f"; about {left / 60:.0f} min left", flush=True)

    print(f"\nDownloaded {total_bytes / 1e9:.1f} GB. Wrote {args.out}. "
          "Source: The GDELT Project, https://www.gdeltproject.org/")


if __name__ == "__main__":
    main()
