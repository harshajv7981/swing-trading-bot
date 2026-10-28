"""Apply today's headline-matching rules to an events file you already have.

    python scripts/refilter_events.py --events data/raw/news/events.csv          # show what would go
    python scripts/refilter_events.py --events data/raw/news/events.csv --write  # and drop it

When `swingbot.news` learns a new false match (another company's name, a job
notice), this removes the GDELT rows it now rejects without downloading
anything again. NSE filing rows are left alone. With --write the old file is
kept next to it as `<events>.before-refilter`.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from collections import Counter
from pathlib import Path

from swingbot.news import EVENT_COLUMNS, HeadlineMatcher, load_universe


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", default="data/raw/news/events.csv")
    p.add_argument("--universe", default="data/universe/nifty100.csv")
    p.add_argument("--write", action="store_true", help="drop the rejected rows (keeps a backup)")
    args = p.parse_args()

    path = Path(args.events)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    kept, dropped = HeadlineMatcher(load_universe(args.universe)).refilter(rows)
    by_ticker = Counter(r["ticker"] for r in dropped)
    print(f"{len(rows)} rows: {len(kept)} kept, {len(dropped)} dropped")
    for t, n in by_ticker.most_common():
        print(f"  {t}: {n}")
    for r in dropped[:20]:
        print(f"  - {r['ticker']} | {r['published_at'][:16]} | {r['headline']}")
    if args.write and dropped:
        shutil.copy2(path, path.with_name(f"{path.name}.before-refilter"))
        tmp = path.with_name(f"{path.name}.tmp")
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=EVENT_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows({c: r.get(c, "") for c in EVENT_COLUMNS} for r in kept)
        tmp.replace(path)
        print(f"wrote {path}; the old file is {path.name}.before-refilter")


if __name__ == "__main__":
    main()
