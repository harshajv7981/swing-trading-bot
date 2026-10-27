"""swingbot-dashboard: one HTML page with the state of the paper account.

    swingbot-dashboard                          # writes runs/dashboard.html
    swingbot-dashboard --open                   # and opens it in the browser
    swingbot-dashboard --dir runs/walkforward   # a walk-forward run's account instead

The page shows the equity curve against the Nifty, the kill switch and the
week's drawdown, the paper gate's progress, open positions with their stops
and open risk, the paper broker's live orders, closed trades, the latest
daily run's steps and the recent alerts.

It only reads: the ledger, the paper broker's book, `kill_switch.json`,
`alerts.jsonl` and the daily runs. It never latches the kill switch, writes
the ledger or fetches anything; prices come from the saved CSVs. The file is
self-contained (no scripts, no web fonts), so it opens offline and can be
attached to a message.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import corporate
from .data.router import DataConfig, DataError, Router
from .models import IST
from .paper.cli import BENCHMARK
from .paper.ledger import DEFAULT_LEDGER, Ledger, replay
from .risk.config import RiskConfig
from .risk.gate import DEFAULT_STATE_DIR
from .technicals import bars_known_at

DEFAULT_OUT = Path("runs/dashboard.html")


@dataclass
class Sources:
    ledger: Path = DEFAULT_LEDGER
    state_dir: Path = DEFAULT_STATE_DIR
    runs_dir: Path = Path("runs/daily")
    data_config: str | None = None
    risk_config: str | None = None

    @classmethod
    def walkforward(cls, folder: Path) -> Sources:
        return cls(folder / "ledger.jsonl", folder / "state", folder / "days", str(folder / "data.toml"))


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def gather(src: Sources, as_of=None) -> dict:
    """Everything the page shows, as plain data. Read-only."""
    as_of = pd.Timestamp(as_of, tz=IST) if as_of is not None else pd.Timestamp.now(IST)
    config = RiskConfig.load(src.risk_config)
    records = corporate.adjust_records(Ledger(src.ledger).records(), corporate.load(src.state_dir))
    out: dict = {"as_of": f"{as_of:%Y-%m-%d %H:%M}", "has_account": any(r["kind"] == "open" for r in records),
                 "kill_pct": config.weekly_kill_pct, "risk_pct": config.risk_pct}
    switch = _read_json(Path(src.state_dir) / "kill_switch.json", {"latched": False})
    out["kill_switch"] = switch
    out["orders"] = [o for o in _read_json(Path(src.state_dir) / "paper_broker.json", {}).get("orders", [])
                     if o.get("status") == "open"]
    out["alerts"] = _read_lines(Path(src.state_dir) / "alerts.jsonl")[-15:][::-1]
    days = sorted(p for p in Path(src.runs_dir).glob("*/") if (p / "day.json").exists()) \
        if Path(src.runs_dir).exists() else []
    out["day"] = None
    if days:
        last = days[-1]
        state = _read_json(last / "day.json", {})
        fin = _read_json(last / "finish.json", {})
        out["day"] = {"date": last.name, "steps": state.get("steps", []) + fin.get("steps", []),
                      "why_not": fin.get("why_not", state.get("why_not", [])),
                      "signals": fin.get("signals", [])}
    if not out["has_account"]:
        return out

    router = Router(DataConfig.load(src.data_config).with_sources(prices="csv"))
    bars, missing = {}, []
    for t in sorted({r["ticker"].upper() for r in records if r["kind"] != "open"}):
        try:
            bars[t] = bars_known_at(router.fetch("prices", t, as_of)[1], as_of)
        except DataError:
            missing.append(t)
    bench = None
    try:
        bench = bars_known_at(router.fetch("prices", BENCHMARK, as_of)[1], as_of)["close"]
        sessions = bench.index
    except DataError:
        sessions = pd.DatetimeIndex(sorted(set().union(*(b.index for b in bars.values())))) if bars \
            else pd.DatetimeIndex([])
    rp = replay(records, bars, as_of, sessions, config)
    s = rp.summary(bench)
    p = rp.portfolio()
    week_dd = (p.week_open_equity - p.equity) / p.week_open_equity * 100 if p.week_open_equity else 0.0
    positions = []
    for h in s["open_positions"]:
        close = rp.closes.get(h["ticker"])
        risk = h["qty"] * max((close or h["avg_entry"]) - (h["stop"] or 0), 0) if h["stop"] else None
        positions.append({**h, "close": close, "open_risk": risk,
                          "pnl": None if close is None else h["qty"] * (close - h["avg_entry"])})
    curve = [{"date": f"{d:%Y-%m-%d}", "equity": float(v)} for d, v in rp.equity.items()]
    if bench is not None and curve:
        b = bench.reindex(rp.equity.index).ffill()
        prior = bench[bench.index < rp.equity.index[0]]
        base = float(prior.iloc[-1]) if len(prior) else float(b.dropna().iloc[0]) if b.notna().any() else None
        for row, v in zip(curve, b.values):
            row["bench"] = None if base is None or pd.isna(v) else rp.starting_cash * float(v) / base
    out.update(summary=s, week_drawdown_pct=max(week_dd, 0.0), positions=positions, curve=curve,
               trades=[t.__dict__ for t in rp.trades][::-1], missing_prices=missing,
               open_risk=sum(x["open_risk"] or 0 for x in positions))
    return out


# --- rendering -----------------------------------------------------------------------

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b675f;--line:#e4e0d8;--card:#fff;--good:#1f7a4d;--bad:#b3261e;
--warn:#9a6700;--accent:#2f5d8a;--bench:#9c968a}
@media (prefers-color-scheme:dark){:root{--bg:#161513;--fg:#ecebe7;--muted:#a19c92;--line:#2f2d29;--card:#1e1d1a;
--good:#4cc38a;--bad:#ff7b72;--warn:#e3b341;--accent:#79a8d8;--bench:#77726a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:20px;margin:0 0 2px}h2{font-size:15px;margin:28px 0 10px}
.sub{color:var(--muted);margin:0 0 18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.tile .k{color:var(--muted);font-size:12px}.tile .v{font-size:20px;font-weight:600;margin-top:2px;
font-variant-numeric:tabular-nums}.tile .n{color:var(--muted);font-size:12px;margin-top:2px}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.banner{border-radius:8px;padding:12px 14px;margin:0 0 16px;font-weight:600;border:1px solid var(--bad);
color:var(--bad);background:transparent}
.bar{height:6px;background:var(--line);border-radius:3px;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}.bar i.hot{background:var(--bad)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:12px}td.num,th.num{text-align:right}
tr:last-child td{border-bottom:0}.empty{color:var(--muted);margin:0}
svg{width:100%;height:auto;display:block}.legend{color:var(--muted);font-size:12px;margin-top:6px}
.legend b{display:inline-block;width:10px;height:3px;vertical-align:middle;margin:0 4px 0 10px}
code{font-size:12px}
"""


def _e(x) -> str:
    return html.escape(str(x))


def _m(x, digits=0) -> str:
    return "–" if x is None else f"{x:,.{digits}f}"


def _signed(x, suffix="%") -> str:
    if x is None:
        return "–"
    cls = "good" if x > 0 else "bad" if x < 0 else ""
    return f'<span class="{cls}">{x:+.2f}{suffix}</span>'


def _rupees(x) -> str:
    if x is None:
        return "–"
    cls = "good" if x > 0 else "bad" if x < 0 else ""
    return f'<span class="{cls}">{x:+,.0f}</span>'


def _tile(k, v, n="", bar=None, hot=False) -> str:
    b = "" if bar is None else f'<div class="bar"><i class="{"hot" if hot else ""}" ' \
                               f'style="width:{max(0, min(bar, 1)) * 100:.0f}%"></i></div>'
    return f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="n">{n}</div>{b}</div>'


def _table(head: list[str], rows: list[list], num: set[int] = frozenset(), empty="Nothing here yet.") -> str:
    if not rows:
        return f'<p class="empty">{empty}</p>'
    th = "".join(f'<th class="{"num" if i in num else ""}">{_e(h)}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i in num else ""}">{c}</td>' for i, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f'<div class="card"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _chart(curve: list[dict]) -> str:
    if len(curve) < 2:
        return '<p class="empty">The equity curve appears after two closed sessions.</p>'
    w, h, pad = 1000, 260, 44
    vals = [r["equity"] for r in curve] + [r["bench"] for r in curve if r.get("bench") is not None]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi * 1.01, lo * 0.99
    x = lambda i: pad + i * (w - pad - 8) / (len(curve) - 1)
    y = lambda v: 8 + (hi - v) * (h - 30) / (hi - lo)

    def path(key):
        pts = [(x(i), y(r[key])) for i, r in enumerate(curve) if r.get(key) is not None]
        return " ".join(f"{'M' if j == 0 else 'L'}{a:.1f},{b:.1f}" for j, (a, b) in enumerate(pts))

    grid = ""
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        grid += (f'<line x1="{pad}" x2="{w - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--line)"/>'
                 f'<text x="{pad - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" '
                 f'fill="var(--muted)">{v / 1000:,.0f}k</text>')
    ticks = ""
    for i in sorted({0, len(curve) // 2, len(curve) - 1}):
        anchor = "start" if i == 0 else "end" if i == len(curve) - 1 else "middle"
        ticks += (f'<text x="{x(i):.1f}" y="{h - 4}" text-anchor="{anchor}" font-size="11" '
                  f'fill="var(--muted)">{curve[i]["date"]}</text>')
    bench = path("bench") if any(r.get("bench") for r in curve) else ""
    svg = (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Equity curve">{grid}{ticks}'
           + (f'<path d="{bench}" fill="none" stroke="var(--bench)" stroke-width="1.5" stroke-dasharray="4 3"/>'
              if bench else "")
           + f'<path d="{path("equity")}" fill="none" stroke="var(--accent)" stroke-width="2"/></svg>')
    legend = '<div class="legend"><b style="background:var(--accent)"></b>Paper equity'
    if bench:
        legend += '<b style="background:var(--bench)"></b>Nifty 50, same starting cash'
    return f'<div class="card">{svg}{legend}</div></div>'


def render(d: dict, title: str = "Swing bot") -> str:
    parts = [f"<h1>{_e(title)}</h1>", (f'<p class="sub">Paper account as of {_e(d["as_of"])} IST. '
                                       "Read-only snapshot: re-run <code>swingbot-dashboard</code> to refresh.</p>")]
    ks = d["kill_switch"]
    if ks.get("latched"):
        parts.append(f'<div class="banner">Kill switch LATCHED: {_e(ks.get("reason", ""))}. No new entries until '
                     "you reset it by hand.</div>")
    if not d["has_account"]:
        parts.append('<p class="empty">No paper account yet: run <code>swingbot-paper open --cash N</code>.</p>')
    else:
        s, g = d["summary"], d["summary"]["gate"]
        dd = d["week_drawdown_pct"]
        parts.append('<div class="tiles">' + "".join([
            _tile("Equity", _m(s["equity"]), f"from {_m(s['starting_cash'])}: {_signed(s['return_pct'])}"),
            _tile("Vs Nifty 50", _signed(s.get("excess_return_pct"), " pts"),
                  f"Nifty {_signed(s.get('benchmark_return_pct'))}" if "benchmark_return_pct" in s else "no Nifty data"),
            _tile("Cash", _m(s["cash"]), f"costs paid {_m(s['costs'])}"),
            _tile("Week drawdown", f"{dd:.2f}%", f"kill switch at {d['kill_pct']:g}%",
                  bar=dd / d["kill_pct"] if d["kill_pct"] else 0, hot=dd >= d["kill_pct"] * 0.6),
            _tile("Open risk", _m(d["open_risk"]),
                  f"{d['open_risk'] / s['equity'] * 100:.2f}% of equity (limit 5%)" if s["equity"] else ""),
            _tile("Max drawdown", f"{s['max_drawdown_pct']:.2f}%", s.get("drawdown_trough") or ""),
            _tile("Closed trades", str(s["closed_trades"]),
                  (f"win {s['win_rate'] * 100:.0f}%, avg {s['avg_r']:+.2f}R" if s["closed_trades"] and s["avg_r"]
                   is not None else "none yet")),
            _tile("Paper gate", "MET" if g["met"] else "not yet",
                  f"{g['months']}/{g['months_needed']} months, {g['trades']}/{g['trades_needed']} trades, "
                  f"{len(s['rule_breaks'])} breaks",
                  bar=min(g["months"] / g["months_needed"], g["trades"] / g["trades_needed"])),
        ]) + "</div>")
        parts += ["<h2>Equity</h2>", _chart(d["curve"])]
        parts += ["<h2>Open positions</h2>", _table(
            ["Stock", "Qty", "Entry", "Last close", "Stop", "Target", "P&L", "Risk to stop", "Since"],
            [[f"<b>{_e(p['ticker'])}</b>", p["qty"], _m(p["avg_entry"], 2), _m(p["close"], 2),
              _m(p["stop"], 2) if p["stop"] else '<span class="bad">NONE</span>', _m(p["target"], 2),
              _rupees(p["pnl"]), _m(p["open_risk"]), _e(p["opened"])]
             for p in d["positions"]], num={1, 2, 3, 4, 5, 6, 7}, empty="No open positions.")]
        if d["missing_prices"]:
            parts.append(f'<p class="sub warn">No saved prices for {_e(", ".join(d["missing_prices"]))}: '
                         "marked at the fill price.</p>")
        breaks = s["rule_breaks"]
        if breaks:
            parts += ["<h2>Rule breaks</h2>", _table(["When", "Rule", "Detail"],
                                                      [[_e(f["at"]), _e(f["rule"]), _e(f["detail"])] for f in breaks])]
    parts += ["<h2>Live orders at the paper broker</h2>", _table(
        ["Id", "Type", "Stock", "Qty", "Limit", "Stop", "Target", "Session"],
        [[_e(o["id"]), _e(o["type"]), _e(o["ticker"]), o["qty"], _m(o.get("limit"), 2),
          _m(o.get("stop"), 2) if o.get("stop") else '<span class="bad">NONE</span>', _m(o.get("target"), 2),
          _e(o.get("session", ""))] for o in d["orders"]], num={3, 4, 5, 6}, empty="No live orders.")]
    if d["has_account"]:
        parts += ["<h2>Closed trades</h2>", _table(
            ["Stock", "Opened", "Closed", "Qty", "Entry", "Exit", "P&L", "R"],
            [[_e(t["ticker"]), _e(t["opened"]), _e(t["closed"]), t["qty"], _m(t["entry"], 2), _m(t["exit"], 2),
              _rupees(t["pnl"]), _signed(t["r_multiple"], "R") if t["r_multiple"] is not None else "–"]
             for t in d["trades"][:50]], num={3, 4, 5, 6, 7}, empty="No closed trades yet.")]
    day = d["day"]
    if day:
        parts.append(f"<h2>Latest daily run: {_e(day['date'])}</h2>")
        if day["why_not"]:
            parts.append(f'<p class="sub">No new entries: {_e("; ".join(day["why_not"]))}.</p>')
        cls = {"ok": "good", "failed": "bad", "blocked": "warn", "stub": "warn"}
        parts.append(_table(["Step", "Status", "Detail"],
                            [[_e(st["name"]), f'<span class="{cls.get(st["status"], "")}">{_e(st["status"])}</span>',
                              f'<span style="white-space:normal">{_e(st["detail"])}</span>'] for st in day["steps"]]))
    parts += ["<h2>Recent alerts</h2>", _table(
        ["Date", "Alert", "Detail"],
        [[_e(a.get("date", "")), f'<span class="{"bad" if a.get("level") == "urgent" else ""}">{_e(a["title"])}</span>',
          f'<span style="white-space:normal">{_e(a["text"])}</span>'] for a in d["alerts"]],
        empty="No alerts yet.")]
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>"
            + "".join(parts) + "</main></body></html>\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-dashboard", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--dir", help="a walk-forward run's folder instead of the live paper account")
    p.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    p.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    p.add_argument("--runs-dir", default="runs/daily")
    p.add_argument("--data-config")
    p.add_argument("--config", help="risk TOML (default config/risk.toml)")
    p.add_argument("--as-of", help="'YYYY-MM-DD HH:MM' IST; default now")
    p.add_argument("--open", action="store_true", help="open the page in the browser")
    args = p.parse_args(argv)
    if args.dir:
        src = Sources.walkforward(Path(args.dir))
        title = f"Swing bot walk-forward: {Path(args.dir).name}"
    else:
        src = Sources(Path(args.ledger), Path(args.state_dir), Path(args.runs_dir), args.data_config, args.config)
        title = "Swing bot paper account"
    try:
        page = render(gather(src, args.as_of), title)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
