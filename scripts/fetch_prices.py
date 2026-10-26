"""Cache NSE daily bars from Yahoo Finance to CSV so runs are repeatable.

    python scripts/fetch_prices.py --events data/my_events.csv \
        --start 2025-01-01 --end 2026-10-01 --out data/raw/prices --benchmark ^NSEI --mood

--mood adds the series swingbot.regime reads (Nifty FMCG, gold ETF, USD/INR,
India VIX). The mood's 200-day check needs about 10 months before the first
date you test, so start early enough.

Needs `pip install -e .[data]`. Check the files before trusting them: Yahoo has
gaps and the odd bad print.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from swingbot.events import load_events
from swingbot.prices import YFinancePriceSource


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", help="take tickers from this events file")
    p.add_argument("--tickers", nargs="*", default=[])
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--out", default="data/raw/prices")
    p.add_argument("--benchmark", default="^NSEI", help="saved as NIFTY50.csv")
    p.add_argument("--mood", action="store_true",
                   help="also save the market-mood series: NIFTYFMCG, GOLDBEES, USDINR, INDIAVIX")
    args = p.parse_args()

    tickers = set(args.tickers)
    if args.mood:
        tickers |= {"NIFTYFMCG", "GOLDBEES", "USDINR", "INDIAVIX"}
    if args.events:
        tickers |= {e.ticker for e in load_events(args.events)}

    src = YFinancePriceSource(args.start, args.end)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for t in sorted(tickers):
        try:
            src.daily(t).to_csv(out / f"{t}.csv")
            print(f"ok   {t}")
        except Exception as e:  # keep going; report which tickers failed
            print(f"FAIL {t}: {e}")

    if args.benchmark:
        src.daily(args.benchmark).to_csv(out / "NIFTY50.csv")
        print(f"ok   {args.benchmark} -> NIFTY50.csv")


if __name__ == "__main__":
    main()
