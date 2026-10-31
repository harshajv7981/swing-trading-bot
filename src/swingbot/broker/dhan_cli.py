"""swingbot-dhan: the Dhan sandbox adapter from a terminal.

    swingbot-dhan check                  # credentials, mode and the account's funds
    swingbot-dhan scrips --refresh       # download Dhan's scrip master, keep the NSE equities
    swingbot-dhan scrips --file x.csv    # or from a copy you saved
    swingbot-dhan submit ticket.json     # one PASSed ticket from `swingbot-risk ticket`
    swingbot-dhan sync                   # fills since the last sync, then the R1 stop check
    swingbot-dhan sync --at-open         # the same, waiting for 09:16 first if run before it
    swingbot-dhan book                   # what the adapter holds
    swingbot-dhan rebalance              # M6 month-end orders from the paper trial's picks: dry run
    swingbot-dhan rebalance --send       # sandbox: send them (sells, then buys)
    swingbot-dhan rebalance --status     # the bot's momentum book
    swingbot-dhan rebalance --halt "why" # stop every momentum order until the halt file is deleted

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
    rb = sub.add_parser("rebalance", help="mid-cap momentum month-end orders (see broker/momentum_dhan.py)")
    rb.add_argument("--send", action="store_true", help="place the orders (sandbox only); default is a dry run")
    rb.add_argument("--status", action="store_true", help="show the bot's momentum book and read fills")
    rb.add_argument("--halt", metavar="WHY", help="write the halt file: no momentum order goes in until it is deleted")
    rb.add_argument("--month", help="YYYY-MM of the booked month to trade (default the newest)")
    rb.add_argument("--prices", help="JSON {symbol: price} to plan with instead of Dhan's quotes (dry run only)")
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
        if args.cmd == "rebalance" and args.halt:
            path = cfg.state_dir / "momentum_halt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{as_of:%Y-%m-%d %H:%M} {args.halt}\n")
            print(f"halted: no momentum order goes in until {path} is deleted")
            return 0
        api = DhanAPI(cfg)
        if args.cmd == "rebalance":
            return _rebalance(args, api, cfg, as_of)
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


def _rebalance(args, api: DhanAPI, cfg: DhanConfig, as_of) -> int:
    from .dhan import load_scrips
    from .momentum_dhan import MomentumConfig, MomentumDhan, describe

    mcfg = MomentumConfig.load(args.config)
    bot = MomentumDhan(api, load_scrips(cfg.scrip_master), mcfg, NSECalendar.load())
    if args.status:
        bot.sync()
        bot.save()
        print(describe(bot.book))
        for n in bot.notes:
            print(f"  note {n}")
        return 0
    if args.send and args.prices:
        print("error: --prices is for dry runs; orders are priced from Dhan's own quotes", file=sys.stderr)
        return 2
    if not mcfg.ledger.exists():
        print(f"error: no paper trial ledger at {mcfg.ledger}; run `swingbot-momentum update` first", file=sys.stderr)
        return 2
    ledger = json.loads(mcfg.ledger.read_text())
    prices = {k.upper(): float(v) for k, v in json.loads(Path(args.prices).read_text()).items()} if args.prices else None
    out = bot.rebalance(ledger, as_of, send=args.send, month=args.month, prices=prices)
    mode = "LIVE account, read-only" if cfg.live else "sandbox"
    print(f"M6 rebalance for the {out['month']} picks ({mode}); slot Rs {out['slot']:,.0f}")
    for o in out["planned"]:
        print(f"  {o['side']:<4} {o['qty']:>6} {o['symbol']:<14} ~{o['price']:,.2f}  Rs {o['qty'] * o['price']:,.0f}")
    if not out["planned"]:
        print("  nothing to trade: the book already matches the picks")
    if args.send:
        for o in out["sent"]:
            print(f"  sent {o['id']} (Dhan {o['dhan_id']}): {o['side'].lower()} {o['qty']} {o['symbol']} "
                  f"limit {o['limit']:,.2f}")
    else:
        print("dry run: nothing sent (add --send in the sandbox)")
    for n in bot.notes:
        print(f"  note {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
