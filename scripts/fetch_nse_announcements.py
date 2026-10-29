"""Collect NSE corporate announcements (exchange filings) into a backtest events file.

    python scripts/fetch_nse_announcements.py --accept-nse-terms \
        --universe data/universe/nifty100.csv data/universe/midcap150.csv \
        --start 2026-07-01 --end 2026-10-15 --out data/raw/news/events.csv

Read this before running it. NSE's terms of use (https://www.nseindia.com/static/nse-terms-of-use)
prohibit "systematic or automated data collection" (clause 9) and storing or
using site content for "virtual trading or simulation activities" (clause 8).
This script breaks those terms. It exists for one person's private research,
runs slowly, and writes under data/raw/, which git ignores. Do not commit,
share or publish what it collects. Expect NSE to block the IP if it is run hard.

The endpoint is the one nseindia.com's own pages call. It is unofficial and can
change without notice.
"""

from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from swingbot.data.remote import NSESession
from swingbot.news import load_universe, merge_into, nse_rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--accept-nse-terms", action="store_true",
                   help="confirm you have read the terms note above and accept the risk")
    p.add_argument("--universe", nargs="+", default=["data/universe/nifty100.csv", "data/universe/midcap150.csv"],
                   help="symbol,name,aliases CSV(s); a symbol in two is read once")
    p.add_argument("--tickers", nargs="*", help="only these symbols from the universe")
    p.add_argument("--start", required=True, type=date.fromisoformat)
    p.add_argument("--end", required=True, type=date.fromisoformat)
    p.add_argument("--chunk-days", type=int, default=31)
    p.add_argument("--pause", type=float, default=4.0, help="seconds between requests; keep it slow")
    p.add_argument("--keep-routine", action="store_true",
                   help="keep paperwork filings (trading window, newspaper ads, ESOP allotments...)")
    p.add_argument("--out", default="data/raw/news/events.csv")
    args = p.parse_args()

    if not args.accept_nse_terms:
        p.error("this script breaks NSE's terms of use; read the note in --help, then pass --accept-nse-terms")
    if not args.out.startswith("data/raw/"):
        print("WARNING: --out is outside data/raw/, so git will not ignore it. Do not commit NSE data.")

    companies = load_universe(args.universe)
    if args.tickers:
        wanted = {t.upper() for t in args.tickers}
        companies = [c for c in companies if c.symbol in wanted]

    nse = NSESession()
    for c in companies:
        raw = []
        day = args.start
        while day <= args.end:
            chunk_end = min(day + timedelta(days=args.chunk_days - 1), args.end)
            try:
                raw += nse.announcements(c.symbol, day, chunk_end)
            except Exception as e:  # keep going; report which chunks failed
                print(f"FAIL {c.symbol} {day}..{chunk_end}: {e}")
            day = chunk_end + timedelta(days=1)
            time.sleep(args.pause)
        rows = nse_rows(raw, keep_routine=args.keep_routine)
        added, total = merge_into(args.out, rows)
        print(f"ok   {c.symbol}: {len(raw)} filings, {len(rows)} kept, {added} new, file now {total}")

    print(f"\nWrote {args.out}. Private use only: do not commit or share it.")


if __name__ == "__main__":
    main()
