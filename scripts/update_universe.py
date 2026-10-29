"""Compare data/universe with NSE's index lists, and add the stocks that joined.

    python scripts/update_universe.py            # show what changed in the Nifty 100
    python scripts/update_universe.py --index midcap150          # the Nifty Midcap 150
    python scripts/update_universe.py --write    # also add the new stocks
    python scripts/update_universe.py --file ind_nifty100list.csv   # a list you saved yourself

Run it on your own machine after each March and September rebalance: the cloud
can't reach niftyindices.com. Stocks that left the index are reported and kept
(one may still be held); remove them by hand once no position is open. New
names get no news aliases, so add common short names to the CSV by hand. A
stock that moves between the two indices stays where it is: one already in the
other file is not added again.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

from swingbot.universe import INDEXES, _read, add_joined, compare, parse_nse_list


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", choices=sorted(INDEXES), default="nifty100")
    p.add_argument("--file", help="a saved copy of NSE's list instead of downloading it")
    p.add_argument("--write", action="store_true", help="append the stocks that joined to both universe files")
    args = p.parse_args()

    url, ours, fewest = INDEXES[args.index]
    if args.file:
        text = Path(args.file).read_text()
    else:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
    members = parse_nse_list(text)
    if len(members) < fewest:
        print(f"error: NSE's list has only {len(members)} rows; not touching anything")
        return 2
    elsewhere = {r["symbol"].strip().upper() for _, other, _ in INDEXES.values() if other != ours
                 for r in _read(other)}
    changes = compare([m for m in members if m.symbol not in elsewhere], ours)
    print(changes.text())
    if args.write and changes.joined:
        add_joined(changes, ours)
        print(f"added {len(changes.joined)} stock(s) to data/universe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
