"""Track a real fund position against Harsha's written limits. Never places an order.

    swingbot-real buy  --date 2027-11-01 --symbol sbimidmom --units 100 --price 52.30
    swingbot-real sell --date 2028-12-01 --symbol sbimidmom --units 40 --price 61.00
    swingbot-real report   # value, after-tax gain, drawdown, cap, tax headroom, M6 alongside
    swingbot-real check    # the nightly job: notify at the review drawdown, in December, and in March

You record what you actually bought or sold, at the price you got. Prices come from eod2_data (NSE-listed
ETFs such as a Midcap150 Momentum 50 ETF or GOLDBEES). Limits come from config/real_money.toml; holdings
live in the git-ignored data/state/real_position.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import date
from pathlib import Path

import pandas as pd

from .momentum import LEDGER, notify

STATE = Path("data/state/real_position.json")
CONFIG = Path("config/real_money.toml")
DATA = Path("data/raw/eod2_data/daily")
STCG, LTCG, LTCG_FREE = 0.208, 0.13, 125_000  # FY 2026-27 with the 4% cess; the exemption is per tax year


def load_state(path: Path = STATE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"lots": [], "sales": [], "notified": []}


def save_state(state: dict, path: Path = STATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)


def load_config(path: Path = CONFIG) -> dict:
    cfg = tomllib.loads(path.read_text())
    return {**cfg["limits"], "targets": cfg.get("targets", {})}


def closes(data: Path, symbol: str) -> pd.Series:
    """Raw closes. shortcut: a split after a buy is not applied to the units; record it by hand if one happens."""
    p = data / f"{symbol.lower()}.csv"
    if not p.exists():
        raise ValueError(f"no price file for {symbol} in {data}")
    s = pd.read_csv(p, usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"].astype(float)
    return s[~s.index.duplicated()].sort_index().dropna()


def tax_year(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def long_term(bought: date, sold: date) -> bool:
    """Listed shares and ETFs: long-term when held more than 12 months."""
    try:
        anniversary = bought.replace(year=bought.year + 1)
    except ValueError:  # bought on 29 Feb
        anniversary = date(bought.year + 1, 3, 1)
    return sold > anniversary


def holdings(state: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for lot in state["lots"]:
        out[lot["symbol"]] = out.get(lot["symbol"], 0.0) + lot["units"]
    return {s: u for s, u in out.items() if u > 1e-9}


def invested(state: dict) -> float:
    return sum(lot["units"] * lot["price"] for lot in state["lots"])


def path(state: dict, data: Path) -> pd.DataFrame:
    """Daily value of what was held and a time-weighted index, so buying more is not counted as a gain."""
    events = [(pd.Timestamp(lot["date"]), lot["symbol"], lot["units"] + 0.0, lot["units"] * lot["price"])
              for lot in state.get("bought", state["lots"])]
    events += [(pd.Timestamp(s["date"]), s["symbol"], -s["units"], -s["units"] * s["price"]) for s in state["sales"]]
    if not events:
        return pd.DataFrame(columns=["value", "index", "drawdown"])
    start = min(e[0] for e in events)
    px = pd.DataFrame({s: closes(data, s) for s in {e[1] for e in events}})
    px = px[px.index >= start].ffill()
    units = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    flow = pd.Series(0.0, index=px.index)
    for d, s, u, cash in events:
        on = px.index[px.index >= d][0]
        units.loc[on:, s] += u
        flow[on] += cash
    value = (units * px).sum(axis=1)
    ret = ((value - flow) / value.shift() - 1).where(value.shift() > 0, 0.0).fillna(0.0)
    idx = (1 + ret).cumprod()
    return pd.DataFrame({"value": value, "index": idx, "drawdown": idx / idx.cummax() - 1})


def drawdown_now(state: dict, data: Path) -> float:
    p = path(state, data)
    return float(p["drawdown"].iloc[-1]) if len(p) else 0.0


def buy(state: dict, cfg: dict, data: Path, d: date, symbol: str, units: float, price: float) -> None:
    """Record a purchase, refusing one the written limits forbid."""
    if d < date.fromisoformat(cfg["start"]):
        raise ValueError(f"the written plan allows no real money before {cfg['start']}")
    if invested(state) + units * price > cfg["cap"]:
        raise ValueError(f"over the Rs {cfg['cap']:,.0f} cap: Rs {invested(state):,.0f} already invested")
    dd = drawdown_now(state, data)
    if dd <= cfg["review_drawdown"]:
        raise ValueError(f"the position is {dd:.0%} from its peak: the plan says add nothing until it recovers")
    lot = {"date": d.isoformat(), "symbol": symbol.lower(), "units": units, "price": price}
    state["lots"].append(dict(lot))
    state.setdefault("bought", []).append(lot)  # every purchase, kept for the value path after sales


def sell(state: dict, d: date, symbol: str, units: float, price: float) -> list[dict]:
    """Sell oldest units first and record each piece's gain, its term and its tax year."""
    symbol, left, pieces = symbol.lower(), units, []
    if holdings(state).get(symbol, 0.0) + 1e-9 < units:
        raise ValueError(f"only {holdings(state).get(symbol, 0.0):g} units of {symbol} held")
    for lot in state["lots"]:
        if lot["symbol"] != symbol or left <= 1e-9 or lot["units"] <= 1e-9:
            continue
        take = min(lot["units"], left)
        bought = date.fromisoformat(lot["date"])
        pieces.append({"date": d.isoformat(), "symbol": symbol, "units": take, "price": price,
                       "gain": take * (price - lot["price"]), "long_term": long_term(bought, d),
                       "tax_year": tax_year(d)})
        lot["units"] -= take
        left -= take
    state["lots"] = [lot for lot in state["lots"] if lot["units"] > 1e-9]
    state["sales"] += pieces
    return pieces


def tax_view(state: dict, data: Path, today: date) -> dict:
    """This tax year's booked gains and how much more long-term gain fits inside the exemption."""
    ty = tax_year(today)
    booked = [s for s in state["sales"] if s["tax_year"] == ty]
    lt = sum(s["gain"] for s in booked if s["long_term"])
    st = sum(s["gain"] for s in booked if not s["long_term"])
    last = {s: float(closes(data, s).iloc[-1]) for s in holdings(state)}
    lt_open = sum(lot["units"] * (last[lot["symbol"]] - lot["price"]) for lot in state["lots"]
                  if long_term(date.fromisoformat(lot["date"]), today))
    room = max(0.0, LTCG_FREE - max(lt, 0.0))
    return {"tax_year": ty, "long_term": lt, "short_term": st, "room": room,
            "harvestable": max(0.0, min(room, lt_open))}


def check(state: dict, cfg: dict, data: Path, today: date, say=notify) -> list[str]:
    """The nightly alerts, each sent once. Returns what was said."""
    said, done = [], set(state["notified"])
    if not state["lots"]:
        return said
    dd = drawdown_now(state, data)
    if dd <= cfg["review_drawdown"] and "drawdown" not in done:
        said.append(f"Real position is {dd:.0%} from its peak: stop adding money and review (written plan)")
        done.add("drawdown")
    elif dd > cfg["review_drawdown"] and "drawdown" in done:
        said.append(f"Real position has recovered to {dd:.0%} from its peak: adding money is allowed again")
        done.discard("drawdown")
    targets = cfg["targets"]
    if today.month == 12 and len(targets) > 1 and f"rebalance-{today.year}" not in done:
        vals = {s: u * float(closes(data, s).iloc[-1]) for s, u in holdings(state).items()}
        total = sum(vals.values())
        drift = ", ".join(f"{s} {vals.get(s, 0) / total:.0%} (target {w:.0%})" for s, w in targets.items())
        said.append(f"December: rebalance the real position to its targets. Now {drift}")
        done.add(f"rebalance-{today.year}")
    tv = tax_view(state, data, today)
    if today.month == 3 and tv["harvestable"] > 0 and f"harvest-{tv['tax_year']}" not in done:
        said.append(f"March: up to Rs {tv['harvestable']:,.0f} of long-term gain fits in this year's "
                    f"Rs 1.25 lakh exemption before 31 March")
        done.add(f"harvest-{tv['tax_year']}")
    for text in said:
        say(text)
    state["notified"] = sorted(done)
    return said


def report(state: dict, cfg: dict, data: Path, today: date) -> str:
    if not state["lots"] and not state["sales"]:
        return f"No real position yet. The written plan allows the first buy from {cfg['start']}.\n"
    lines, p = ["# Real position", ""], path(state, data)
    for s, u in holdings(state).items():
        lots = [lot for lot in state["lots"] if lot["symbol"] == s]
        cost, px = sum(lot["units"] * lot["price"] for lot in lots), float(closes(data, s).iloc[-1])
        lines.append(f"- {s}: {u:g} units at {px:,.2f} = Rs {u * px:,.0f} (cost Rs {cost:,.0f}, "
                     f"gain Rs {u * px - cost:+,.0f})")
    tv = tax_view(state, data, today)
    lines += ["", f"Invested Rs {invested(state):,.0f} of the Rs {cfg['cap']:,.0f} cap.",
              f"Time-weighted return since the first buy {p['index'].iloc[-1] - 1:+.1%}; "
              f"{p['drawdown'].iloc[-1]:.0%} from the peak (review at {cfg['review_drawdown']:.0%}).",
              f"Tax year {tv['tax_year']}: booked long-term Rs {tv['long_term']:+,.0f}, short-term "
              f"Rs {tv['short_term']:+,.0f}; Rs {tv['harvestable']:,.0f} more long-term gain fits in the exemption."]
    if LEDGER.exists():
        led = json.loads(LEDGER.read_text())
        if led["months"]:
            lines.append(f"M6 paper trial alongside: {led['months'][-1]['value'] / led['capital'] - 1:+.1%} "
                         f"since {led['months'][0]['date']} (paper, before tax).")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-real", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("buy", "sell"):
        c = sub.add_parser(name, help=f"record a real {name} you already made")
        c.add_argument("--date", required=True, type=date.fromisoformat)
        c.add_argument("--symbol", required=True)
        c.add_argument("--units", required=True, type=float)
        c.add_argument("--price", required=True, type=float)
    sub.add_parser("report", help="value, after-tax view, drawdown and limits")
    sub.add_parser("check", help="nightly alerts: review drawdown, December rebalance, March harvest")
    args = ap.parse_args(argv)
    state, cfg = load_state(), load_config()
    try:
        if args.cmd == "buy":
            buy(state, cfg, DATA, args.date, args.symbol, args.units, args.price)
        elif args.cmd == "sell":
            for s in sell(state, args.date, args.symbol, args.units, args.price):
                print(f"sold {s['units']:g} {s['symbol']}: gain Rs {s['gain']:+,.0f}, "
                      f"{'long' if s['long_term'] else 'short'}-term, tax year {s['tax_year']}")
        elif args.cmd == "check":
            check(state, cfg, DATA, date.today())
        else:
            print(report(state, cfg, DATA, date.today()), end="")
            return 0
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
