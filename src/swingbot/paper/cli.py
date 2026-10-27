"""swingbot-paper: the paper trading account.

The paper broker takes tickets from `swingbot-risk ticket` and fills them from
daily bars (entry limit, OCO stop and target, gaps at the open, R7 trailing),
writing each fill to the ledger. Fills can still be recorded by hand. The ledger
is replayed into an account:
cash, positions, a daily equity curve, the worst drawdown, the return against
the Nifty, and every rule a trade broke. It also writes the portfolio file the
risk engine reads, so sizing and the gate see the real paper account.

    swingbot-paper open --cash 1000000 --at 2026-11-02
    swingbot-paper submit runs/risk/ticket.json --at "2026-11-02 18:00"
    swingbot-paper settle --as-of "2026-11-03 18:00" --trail
    swingbot-paper orders
    swingbot-paper close RELIANCE        # sell at the next open
    swingbot-paper fill buy --order runs/risk/order.json --qty 30 --price 1451.5 --at "2026-11-03 09:20"
    swingbot-paper fill sell RELIANCE --qty 30 --price 1530 --at "2026-11-10 10:05"
    swingbot-paper stop RELIANCE 1450 --at "2026-11-06 15:40"
    swingbot-paper report
    swingbot-paper portfolio --out portfolio.json

Nothing here talks to a broker. Exit codes: 0 done, 1 rule breaks found, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..data.router import DataConfig, DataError, Router, parse_as_of
from ..models import IST
from ..risk.calendar import NSECalendar
from ..risk.config import RiskConfig
from ..broker import PaperBroker, record_fills
from ..broker.paper import DEFAULT_BOOK
from ..risk.gate import DEFAULT_STATE_DIR, KillSwitch
from ..risk.portfolio import Order, load_sectors
from ..technicals import bars_known_at
from .ledger import DEFAULT_COST_BPS, DEFAULT_LEDGER, Ledger, default_costs, replay

BENCHMARK = "NIFTY50"


def _at(value: str | None) -> str:
    ts = pd.Timestamp(value) if value else pd.Timestamp(datetime.now(IST))
    ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
    return f"{ts:%Y-%m-%d %H:%M}"


def _verdict_for(order: Order, day: str, state_dir: str) -> str:
    """PASS if `swingbot-risk check` passed this exact order on the fill's day."""
    log = Path(state_dir) / "risk_verdicts.jsonl"
    if not log.exists():
        return "none"
    for line in reversed(log.read_text().splitlines()):
        if not line.strip():
            continue
        v = json.loads(line)
        if v.get("fingerprint") == order.fingerprint and str(v.get("at", "")).startswith(day):
            return v.get("status", "none")
    return "none"


def cmd_open(args, ledger: Ledger, config) -> int:
    if args.cash <= 0:
        print("error: --cash must be above 0", file=sys.stderr)
        return 2
    ledger.append({"kind": "open", "at": _at(args.at), "cash": args.cash})
    print(f"paper account opened with {args.cash:,.0f} on {_at(args.at)[:10]}")
    return 0


def cmd_fill(args, ledger: Ledger, config) -> int:
    at = _at(args.at)
    rec = {"kind": "fill", "at": at, "side": args.side, "qty": args.qty, "price": args.price}
    if args.order:
        order = Order.from_json(json.loads(Path(args.order).read_text()))
        if args.ticker and args.ticker.upper() != order.ticker:
            print(f"error: {args.order} is for {order.ticker}, not {args.ticker.upper()}", file=sys.stderr)
            return 2
        if order.mood.get("verdict"):
            rec["mood"] = order.mood["verdict"]
        rec.update(ticker=order.ticker, stop=order.stop, target=order.target, sector=order.sector,
                   fingerprint=order.fingerprint, verdict=_verdict_for(order, at[:10], args.state_dir))
    elif args.ticker:
        rec["ticker"] = args.ticker.upper()
        if args.side == "buy":
            rec.update(stop=args.stop, target=args.target,
                       sector=args.sector or load_sectors().get(args.ticker.upper(), ""), verdict="none")
    else:
        print("error: give a TICKER or --order", file=sys.stderr)
        return 2
    rec["costs"] = args.costs if args.costs is not None else default_costs(args.qty, args.price, args.cost_bps)
    if args.side == "buy" and rec.get("verdict") != "PASS":
        print("warning: no PASS from `swingbot-risk check` for this exact order today; "
              "the report will list it as a rule break", file=sys.stderr)
    ledger.append(rec)
    print(f"recorded {args.side} {args.qty} {rec['ticker']} @ {args.price:,.2f} on {at} "
          f"(costs {rec['costs']:,.2f})")
    return 0


def cmd_stop(args, ledger: Ledger, config) -> int:
    broker = _broker(args, config)
    if any(o["type"] == "oco" and o["ticker"] == args.ticker.upper() for o in broker.open_orders()):
        move = broker.modify_stop(args.ticker, args.price, _at(args.at))
        record_fills(ledger, [move])
        broker.save()
        print(f"paper broker: {move.ticker} stop moved to {move.stop:,.2f}")
        return 0
    ledger.append({"kind": "stop", "at": _at(args.at), "ticker": args.ticker.upper(), "stop": args.price})
    print(f"recorded {args.ticker.upper()} stop {args.price:,.2f}")
    return 0


def _broker(args, config) -> PaperBroker:
    return PaperBroker(args.book, config, NSECalendar.load(), trail=getattr(args, "trail", False))


def cmd_submit(args, ledger: Ledger, config) -> int:
    if not any(r["kind"] == "open" for r in ledger.records()):
        print("error: the ledger has no opening balance; run `swingbot-paper open --cash N` first", file=sys.stderr)
        return 2
    broker = _broker(args, config)
    ticket = json.loads(Path(args.ticket).read_text())
    oid = broker.submit(ticket, _at(args.at), kill_switch_latched=KillSwitch(args.state_dir).latched)
    broker.save()
    o = broker.orders[-1]
    print(f"paper broker {oid}: buy {o['qty']} {o['ticker']} limit {o['limit']:,.2f} for {o['session']} only; "
          f"OCO stop {o['stop']:,.2f} / target {o['target']:,.2f} from the session after it fills")
    return 0


def cmd_settle(args, ledger: Ledger, config) -> int:
    as_of = parse_as_of(args.as_of)
    broker = _broker(args, config)
    router = Router(DataConfig.load(args.data_config))
    bars: dict[str, pd.DataFrame] = {}
    for t in sorted({o["ticker"] for o in broker.open_orders()}):
        try:
            bars[t] = bars_known_at(router.fetch("prices", t, as_of)[1], as_of)
        except DataError as e:
            print(f"note: no prices for {t} ({e})", file=sys.stderr)
    events = broker.sync(as_of, bars)
    record_fills(ledger, events)  # the ledger first: the book is saved only once the account has the fills
    broker.save()
    for e in events:
        if hasattr(e, "side"):
            print(f"{e.at}  {e.side} {e.qty} {e.ticker} @ {e.price:,.2f} ({e.reason})  [{e.order_id}]")
        else:
            print(f"{e.at}  {e.ticker} stop -> {e.stop:,.2f} ({e.reason})  [{e.order_id}]")
    for n in broker.notes:
        print(f"note: {n}")
    print(f"settled to {broker.last_session or 'nothing yet'}: {len(events)} ledger lines written, "
          f"{len(broker.open_orders())} orders live")
    return 0


def cmd_orders(args, ledger: Ledger, config) -> int:
    broker = _broker(args, config)
    live = broker.open_orders()
    if args.json:
        print(json.dumps(live, indent=2))
        return 0
    if not live:
        print(f"no live orders (settled to {broker.last_session or 'nothing yet'})")
    for o in live:
        if o["type"] == "entry":
            print(f"{o['id']} entry  buy {o['qty']} {o['ticker']} limit {o['limit']:,.2f} on {o['session']}")
        elif o["type"] == "oco":
            print(f"{o['id']} OCO    {o['qty']} {o['ticker']} bought {o['entry_price']:,.2f} on {o['entry_session']}: "
                  f"stop {o['stop']:,.2f}, target {o['target']:,.2f}, live from {o['live_from']}")
        else:
            print(f"{o['id']} close  sell {o['qty']} {o['ticker']} at the open on {o['session']}")
    return 0


def cmd_cancel(args, ledger: Ledger, config) -> int:
    broker = _broker(args, config)
    broker.cancel(args.order_id, _at(args.at))
    broker.save()
    print(f"cancelled {args.order_id}")
    return 0


def cmd_close(args, ledger: Ledger, config) -> int:
    broker = _broker(args, config)
    oid = broker.close(args.ticker, _at(args.at))
    broker.save()
    print(f"paper broker {oid}: sell {broker.orders[-1]['qty']} {args.ticker.upper()} at the open on "
          f"{broker.orders[-1]['session']}")
    return 0


def _replay(args, ledger: Ledger, config):
    as_of = parse_as_of(args.as_of)
    records = ledger.records()
    router = Router(DataConfig.load(args.data_config))
    tickers = sorted({r["ticker"].upper() for r in records if r["kind"] != "open"})
    bars: dict[str, pd.DataFrame] = {}
    for t in tickers:
        try:
            bars[t] = bars_known_at(router.fetch("prices", t, as_of)[1], as_of)
        except DataError as e:
            print(f"note: no prices for {t} ({e}); it is marked at its fill price", file=sys.stderr)
    bench = None
    try:
        bench = bars_known_at(router.fetch("prices", BENCHMARK, as_of)[1], as_of)["close"]
        sessions = bench.index
    except DataError as e:
        print(f"note: no {BENCHMARK} prices ({e}); no benchmark, sessions taken from the stocks", file=sys.stderr)
        sessions = pd.DatetimeIndex(sorted(set().union(*(b.index for b in bars.values())))) if bars \
            else pd.DatetimeIndex([])
    rp = replay(records, bars, as_of, sessions, config)
    # R5 latches on the drawdown itself, not only when an order is checked.
    week, reason = rp.weekly_breach(config)
    if week and KillSwitch(args.state_dir).latch(reason, week):
        print(f"kill switch latched: {reason}", file=sys.stderr)
    return rp, bench


def _money(x) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def cmd_report(args, ledger: Ledger, config) -> int:
    rp, bench = _replay(args, ledger, config)
    s = rp.summary(bench)
    bench_line = (f"; Nifty {s['benchmark_return_pct']:+.2f}%, so {s['excess_return_pct']:+.2f} points vs the index"
                  if "benchmark_return_pct" in s else "; no benchmark")
    g = s["gate"]
    lines = [
        f"Paper account {s['start']} to {s['as_of']} ({s['sessions']} sessions)",
        f"Equity {_money(s['equity'])} from {_money(s['starting_cash'])}: {s['return_pct']:+.2f}%{bench_line}",
        f"Cash {_money(s['cash'])}; costs paid {_money(s['costs'])}",
        f"Max drawdown {s['max_drawdown_pct']:.2f}%"
        + (f" ({s['drawdown_peak']} to {s['drawdown_trough']})" if s["drawdown_peak"] else ""),
        f"Closed trades {s['closed_trades']}"
        + (f": win rate {s['win_rate'] * 100:.0f}%, average {s['avg_r']:+.2f}R, "
           f"expectancy {_money(s['expectancy'])} per trade, profit factor "
           f"{'n/a' if s['profit_factor'] is None else s['profit_factor']}" if s["closed_trades"] else ""),
        f"Paper gate: {g['months']} of {g['months_needed']} months, {g['trades']} of {g['trades_needed']} "
        f"trades, {len(s['rule_breaks'])} rule breaks: {'MET' if g['met'] else 'not met yet'}",
    ]
    for h in s["open_positions"]:
        close, stop = rp.closes.get(h["ticker"]), h["stop"]
        lines.append(f"  holding {h['qty']} {h['ticker']} from {h['opened']} at {h['avg_entry']:,.2f}, "
                     f"stop {'none' if stop is None else f'{stop:,.2f}'}, "
                     f"last close {'n/a' if close is None else f'{close:,.2f}'}")
    for f in s["rule_breaks"]:
        lines.append(f"  BREAK {f['at']} {f['rule']}: {f['detail']}")
    for f in s["warnings"]:
        lines.append(f"  note  {f['at']} {f['rule']}: {f['detail']}")
    print("\n".join(lines))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text(json.dumps(s, indent=2, default=str) + "\n")
        rp.equity.rename("equity").rename_axis("date").to_csv(out / "equity.csv")
        pd.DataFrame([t.__dict__ for t in rp.trades]).to_csv(out / "trades.csv", index=False)
        print(f"wrote {out}/summary.json, equity.csv and trades.csv")
    return 1 if s["rule_breaks"] else 0


def cmd_portfolio(args, ledger: Ledger, config) -> int:
    rp, _ = _replay(args, ledger, config)
    text = json.dumps(_portfolio_json(rp.portfolio()), indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def _portfolio_json(p) -> dict:
    return {"equity": p.equity, "cash": p.cash, "week_open_equity": p.week_open_equity,
            "day_open_equity": p.day_open_equity, "positions": [pos.__dict__ for pos in p.positions]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-paper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER), help=f"ledger file (default {DEFAULT_LEDGER})")
    p.add_argument("--config", help="risk limits TOML (default config/risk.toml)")
    p.add_argument("--data-config", help="data sources TOML (default config/data_sources.toml)")
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR), help="swingbot-risk's verdict log and kill switch")
    p.add_argument("--book", default=str(DEFAULT_BOOK), help=f"paper broker order book (default {DEFAULT_BOOK})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("open", help="start the account with its opening cash")
    s.add_argument("--cash", type=float, required=True)
    s.add_argument("--at", help="YYYY-MM-DD [HH:MM] IST; default now")

    s = sub.add_parser("fill", help="record one fill from the paper broker")
    s.add_argument("side", choices=["buy", "sell"])
    s.add_argument("ticker", nargs="?", help="not needed with --order")
    s.add_argument("--order", help="the order file `swingbot-risk check` passed (its stop, target, sector)")
    s.add_argument("--qty", type=int, required=True)
    s.add_argument("--price", type=float, required=True, help="the fill price")
    s.add_argument("--at", help="fill time, YYYY-MM-DD HH:MM IST; default now")
    s.add_argument("--stop", type=float, help="buy without --order: the stop")
    s.add_argument("--target", type=float, help="buy without --order: the target")
    s.add_argument("--sector")
    s.add_argument("--costs", type=float, help="charges in rupees from the contract note")
    s.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS,
                   help=f"charges estimate when --costs is not given (default {DEFAULT_COST_BPS} bps a side)")

    s = sub.add_parser("submit", help="give the paper broker a ticket from `swingbot-risk ticket`")
    s.add_argument("ticket")
    s.add_argument("--at", help="YYYY-MM-DD HH:MM IST; default now. The entry is live for the next session only")

    s = sub.add_parser("settle", help="fill the book from every session closed by --as-of, into the ledger")
    s.add_argument("--as-of", help="YYYY-MM-DD or 'YYYY-MM-DD HH:MM' IST; default now")
    s.add_argument("--trail", action="store_true", help="after each close, trail held stops by R7")

    s = sub.add_parser("orders", help="the paper broker's live orders")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("cancel", help="cancel an entry that has not filled")
    s.add_argument("order_id")
    s.add_argument("--at")

    s = sub.add_parser("close", help="sell a paper broker holding at the next open")
    s.add_argument("ticker")
    s.add_argument("--at")

    s = sub.add_parser("stop", help="move a stop (it may only move up); goes through the paper broker if it holds it")
    s.add_argument("ticker")
    s.add_argument("price", type=float)
    s.add_argument("--at")

    for name, help_ in (("report", "equity, drawdown, return vs the Nifty, trades and rule breaks"),
                        ("portfolio", "the portfolio file swingbot-risk reads")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--as-of", help="YYYY-MM-DD or 'YYYY-MM-DD HH:MM' IST; default now")
        s.add_argument("--out", help="report: a directory; portfolio: a JSON file")

    args = p.parse_args(argv)
    commands = {"open": cmd_open, "fill": cmd_fill, "stop": cmd_stop, "report": cmd_report,
                "portfolio": cmd_portfolio, "submit": cmd_submit, "settle": cmd_settle, "orders": cmd_orders,
                "cancel": cmd_cancel, "close": cmd_close}
    try:
        return commands[args.cmd](args, Ledger(args.ledger), RiskConfig.load(args.config))
    except (DataError, FileNotFoundError, ValueError, KeyError) as e:  # a refused order is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
