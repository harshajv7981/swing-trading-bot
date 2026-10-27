"""swingbot-dhan: the Dhan sandbox adapter from a terminal.

    swingbot-dhan check                  # credentials, mode and the account's funds
    swingbot-dhan scrips --refresh       # download Dhan's scrip master, keep the NSE equities
    swingbot-dhan scrips --file x.csv    # or from a copy you saved
    swingbot-dhan submit ticket.json     # one PASSed ticket from `swingbot-risk ticket`
    swingbot-dhan sync                   # fills since the last sync, then the R1 stop check
    swingbot-dhan sync --at-open         # the same, waiting for 09:16 first if run before it
    swingbot-dhan book                   # what the adapter holds

Sandbox unless config/broker.toml and SWINGBOT_DHAN_LIVE=yes both say live
(R11), and live is read-only. Fills go to the sandbox ledger
(data/state/dhan_sandbox_ledger.jsonl by default), never the paper ledger.
Exit codes: 0 done, 1 Dhan refused or a stop could not be confirmed, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from time import sleep

from ..paper.ledger import Ledger
from ..risk.calendar import NSECalendar
from ..risk.config import RiskConfig
from ..risk.gate import KillSwitch
from .base import OrderRefused, record_fills
from .dhan import (
    SCRIP_MASTER_URL,
    DhanAPI,
    DhanBroker,
    DhanConfig,
    DhanError,
    parse_scrip_master,
    save_scrips,
    wait_for_open,
)

DEFAULT_LEDGER = Path("data/state/dhan_sandbox_ledger.jsonl")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-dhan", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="broker TOML (default config/broker.toml)")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.add_argument("--as-of", help="'YYYY-MM-DD HH:MM' IST; default now")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sc = sub.add_parser("scrips")
    sc.add_argument("--refresh", action="store_true")
    sc.add_argument("--file")
    s = sub.add_parser("submit")
    s.add_argument("ticket")
    sy = sub.add_parser("sync")
    sy.add_argument("--at-open", action="store_true",
                    help="before 09:16 on a session day, wait until then (the stops only go in while the "
                         "market is open)")
    sub.add_parser("book")
    args = p.parse_args(argv)

    import pandas as pd

    from ..models import IST
    as_of = pd.Timestamp(args.as_of, tz=IST) if args.as_of else pd.Timestamp.now(IST)
    try:
        cfg = DhanConfig.load(args.config)
        if args.cmd == "scrips":
            if args.file:
                text = Path(args.file).read_text()
            elif args.refresh:
                with urllib.request.urlopen(SCRIP_MASTER_URL, timeout=60) as resp:
                    text = resp.read().decode("utf-8", "replace")
            else:
                print("error: give --refresh or --file", file=sys.stderr)
                return 2
            scrips = parse_scrip_master(text)
            if len(scrips) < 500:
                print(f"error: only {len(scrips)} NSE equities in that file; not saving it", file=sys.stderr)
                return 2
            save_scrips(scrips, cfg.scrip_master)
            print(f"saved {len(scrips)} NSE equity security ids to {cfg.scrip_master}")
            return 0
        if args.cmd == "book":
            path = cfg.state_dir / "dhan_book.json"
            print(path.read_text() if path.exists() else f"no book yet at {path}")
            return 0
        api = DhanAPI(cfg)
        if args.cmd == "check":
            funds = api.call("GET", "/fundlimit")
            print(f"Dhan {'LIVE (read-only)' if cfg.live else 'sandbox'} at {api.base}, client {api.client_id}")
            print(f"available balance {float(funds.get('availabelBalance', funds.get('availableBalance', 0))):,.2f}")
            return 0
        calendar = NSECalendar.load()
        broker = DhanBroker(api, RiskConfig.load(), calendar)
        if args.cmd == "submit":
            ticket = json.loads(Path(args.ticket).read_text())
            oid = broker.submit(ticket, as_of, kill_switch_latched=KillSwitch(cfg.state_dir).latched)
            broker.save()
            o = broker.orders[-1]
            print(f"{oid} (Dhan {o['dhan_id']}): buy {o['qty']} {o['ticker']} limit {o['limit']:,.2f} for {o['session']}")
            return 0
        if args.cmd == "sync":
            if args.at_open and (wait := wait_for_open(as_of, calendar)) > 0:
                print(f"waiting {wait / 60:.0f} min for 09:16, when Dhan takes the stops")
                sleep(wait)
                as_of = pd.Timestamp.now(IST) if not args.as_of else as_of + pd.Timedelta(seconds=wait)
            events = broker.sync(as_of)
            record_fills(Ledger(args.ledger), events)   # the ledger first, then the book
            broker.save()
            for e in events:
                print(f"  {e.side} {e.qty} {e.ticker} @ {e.price:,.2f} ({e.reason})")
            for n in broker.notes:
                print(f"  note {n}")
            print(f"{len(events)} fill(s); {len(broker.open_orders())} live here")
            return 1 if any("NO confirmed stop" in n for n in broker.notes) else 0
    except OrderRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    except DhanError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
