"""The M6 rebalance journal: what each month's Dhan orders really cost against the backtest.

    swingbot-dhan rebalance --journal      # writes runs/momentum-trial/journal.md

The backtest and the paper ledger trade every stock at the month-end close and
charge 0.20% a side for everything. The bot trades later, at marketable limits
from a live quote. This reads the bot's book (`momentum_dhan_book.json`), and
the paper ledger for each month's closes, and for every order records:

* the fill against the quote it was planned from (`ref_price`): the cost of
  crossing the spread and of the market moving while the orders worked;
* the fill against the month-end close the paper ledger booked: the whole gap
  between the backtest's assumption and what really happened;
* what did not fill (rejected, cancelled, lapsed at the close) and part fills.

Slippage is in basis points, signed so a positive number is a cost (paid more
on a buy, got less on a sell), and averaged by the value traded. A fill more
than 20% from its limit is the sandbox's placeholder price or a data glitch
(it also halts the bot): it is listed but left out of the averages.

It only reads; it never calls Dhan. In the sandbox the fills are placeholders,
so the numbers there test the plumbing, not the costs.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..momentum import COST_SIDE
from .momentum_dhan import BOOK, ODD_FILL

OUT = Path("runs/momentum-trial/journal.md")


def closes(ledger: dict | None) -> dict[str, dict[str, float]]:
    """{month-end date: {symbol: the close the paper ledger booked that day}}."""
    out = {}
    for m in (ledger or {}).get("months", []):
        px = {s: h["price"] for s, h in m.get("holdings", {}).items()}
        px.update({o["symbol"]: o["price"] for o in m.get("orders", [])})
        out[m["date"]] = px
    return out


def _bps(side: str, fill: float, ref: float | None) -> float | None:
    if not ref:
        return None
    move = fill / ref - 1
    return (move if side == "BUY" else -move) * 1e4


def rows(book: dict, ledger: dict | None = None) -> list[dict]:
    """One row per order the bot sent, oldest first."""
    close = closes(ledger)
    out = []
    for o in book.get("orders", []):
        filled = int(o.get("filled") or 0)
        avg = float(o["avg_price"]) if filled and o.get("avg_price") else None
        c = close.get(o["month"], {}).get(o["symbol"])
        suspect = avg is not None and abs(avg / o["limit"] - 1) > ODD_FILL
        out.append({"id": o["id"], "month": o["month"], "sent_at": o["sent_at"], "symbol": o["symbol"],
                    "side": o["side"], "qty": int(o["qty"]), "filled": filled, "status": o["status"],
                    "ref_price": o.get("ref_price"), "limit": o["limit"], "avg_price": avg, "close": c,
                    "vs_quote_bps": _bps(o["side"], avg, o.get("ref_price")) if avg else None,
                    "vs_close_bps": _bps(o["side"], avg, c) if avg else None, "suspect": suspect})
    return out


def _weighted(rs: list[dict], key: str) -> float | None:
    pairs = [(r[key], r["filled"] * r["avg_price"]) for r in rs if r[key] is not None]
    w = sum(v for _, v in pairs)
    return sum(b * v for b, v in pairs) / w if w else None


def months(rs: list[dict]) -> list[dict]:
    """Per month of picks: fill rates, value traded and value-weighted slippage."""
    out = []
    for me in sorted({r["month"] for r in rs}):
        mine = [r for r in rs if r["month"] == me]
        ok = [r for r in mine if r["filled"] and not r["suspect"]]
        want = sum(r["qty"] * (r["ref_price"] or r["limit"]) for r in mine)
        got = sum(r["filled"] * (r["ref_price"] or r["limit"]) for r in mine)
        out.append({"month": me, "orders": len(mine),
                    "full": sum(r["filled"] == r["qty"] for r in mine),
                    "part": sum(0 < r["filled"] < r["qty"] for r in mine),
                    "none": sum(r["filled"] == 0 for r in mine),
                    "fill_rate": got / want if want else None,
                    "traded": sum(r["filled"] * r["avg_price"] for r in ok),
                    "vs_quote_bps": _weighted(ok, "vs_quote_bps"), "vs_close_bps": _weighted(ok, "vs_close_bps"),
                    "suspect": sum(r["suspect"] for r in mine),
                    "unfilled": [f"{r['side'].lower()} {r['symbol']} ({r['status']})" for r in mine
                                 if r["filled"] < r["qty"]]})
    return out


def _f(x, fmt) -> str:
    return "–" if x is None else format(x, fmt)


def render(rs: list[dict], live: bool) -> str:
    ms = months(rs)
    ok = [r for r in rs if r["filled"] and not r["suspect"]]
    lines = ["# M6 rebalance journal", "",
             f"Account: {'LIVE (read-only)' if live else 'Dhan sandbox'}. The backtest assumes fills at the "
             f"month-end close and {COST_SIDE:.2%} a side for all costs."]
    if not live:
        lines += ["", "The sandbox fills at placeholder prices, so its slippage is not a cost estimate."]
    if not rs:
        return "\n".join(lines + ["", "No momentum orders sent yet.", ""])
    q, c = _weighted(ok, "vs_quote_bps"), _weighted(ok, "vs_close_bps")
    lines += ["", "## So far", "",
              f"- {len(rs)} orders over {len(ms)} month(s); {sum(m['full'] for m in ms)} filled in full, "
              f"{sum(m['part'] for m in ms)} in part, {sum(m['none'] for m in ms)} not at all.",
              f"- Slippage, weighted by value: {_f(q, '+.0f')} bps against the planning quote, "
              f"{_f(c, '+.0f')} bps against the month-end close.",
              f"- Against the backtest: {_f(c, '+.0f')} bps a side on top of its {COST_SIDE * 1e4:.0f} bps "
              f"(positive means the backtest was too kind)."]
    sus = [r for r in rs if r["suspect"]]
    if sus:
        lines.append(f"- {len(sus)} fill(s) more than {ODD_FILL:.0%} from the limit, left out of the averages: "
                     + ", ".join(f"{r['id']} {r['symbol']} @ {r['avg_price']:,.2f}" for r in sus) + ".")
    lines += ["", "## By month", "",
              "| Picks of | Orders | Full | Part | None | Filled (by value) | Traded | vs quote | vs close |",
              "|---|---|---|---|---|---|---|---|---|"]
    for m in reversed(ms):
        lines.append(f"| {m['month']} | {m['orders']} | {m['full']} | {m['part']} | {m['none']} | "
                     f"{_f(m['fill_rate'], '.0%')} | Rs {m['traded']:,.0f} | {_f(m['vs_quote_bps'], '+.0f')} bps | "
                     f"{_f(m['vs_close_bps'], '+.0f')} bps |")
    lines += ["", "## Orders", "",
              "| Id | Sent | Picks of | Stock | Side | Qty | Filled | Status | Quote | Limit | Fill | Close | "
              "vs quote | vs close |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reversed(rs):
        flag = " (suspect)" if r["suspect"] else ""
        lines.append(f"| {r['id']} | {r['sent_at']} | {r['month']} | {r['symbol']} | {r['side'].lower()} | "
                     f"{r['qty']} | {r['filled']} | {r['status']}{flag} | {_f(r['ref_price'], ',.2f')} | "
                     f"{r['limit']:,.2f} | {_f(r['avg_price'], ',.2f')} | {_f(r['close'], ',.2f')} | "
                     f"{_f(r['vs_quote_bps'], '+.0f')} | {_f(r['vs_close_bps'], '+.0f')} |")
    return "\n".join(lines) + "\n"


def write(state_dir: Path, ledger_path: Path, live: bool, out: Path = OUT) -> tuple[Path, list[dict]]:
    book_path = state_dir / BOOK
    book = json.loads(book_path.read_text()) if book_path.exists() else {"orders": []}
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else None
    rs = rows(book, ledger)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rs, live))
    return out, months(rs)
