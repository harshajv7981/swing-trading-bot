"""Phase 1 US history study (docs/research/us-research-prereg.md). Research only.

    swingbot-us-research panel [--start 2016-01-01] [--end YYYY-MM-DD] [--out data/raw/us_panel]
    swingbot-us-research study [--panel data/raw/us_panel] [--out data/state/markets/us/research]

`panel` freezes Alpaca's adjusted daily closes for every symbol that was in
the S&P 500 at any time in the window (members rebuilt from Wikipedia's change
table), with a manifest and a SHA-256. `study` reads only the frozen panel, so
the same panel always gives the same report. The panel is Alpaca's data and is
never committed; the report and the membership file are.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import re
import sys
import urllib.request
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from .broker.alpaca import AlpacaError
from .us_momentum import BENCH, SP500_URL, AlpacaData, month_ends, parse_sp500, replay

CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
COSTS = (0.0005, 0.0015)          # a side: the trial's slippage, and the stress test
STRATEGIES = ("M12", "M6", "TREND", "M12+TREND")


# --- members over time ------------------------------------------------------------------


class _Changes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.row, self.cell, self.rows = 0, None, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "table" and (self.depth or dict(attrs).get("id") == "changes"):
            self.depth += 1
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag == "td" and self.row is not None:
            self.cell = ""

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
        elif self.depth == 1 and tag == "td" and self.cell is not None:
            self.row.append(re.sub(r"\[.*?\]", "", self.cell).strip())
            self.cell = None
        elif self.depth == 1 and tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data


def _date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%B %d, %Y").date()
    except ValueError:
        return None


def parse_changes(html: str) -> list[tuple[date, str, str]]:
    """(date, added ticker, removed ticker) rows of Wikipedia's S&P 500 change table, newest first."""
    p = _Changes()
    p.feed(html)
    out, last = [], None
    for cells in p.rows:
        when = _date(cells[0]) if cells else None
        if when:
            last, cells = when, cells[1:]
        if last is None or len(cells) < 4:
            continue
        out.append((last, cells[0], cells[2]))
    if not out:
        raise ValueError("no S&P 500 change table found in the page")
    return out


class Membership:
    """Members at a date: today's list with later changes undone."""

    def __init__(self, current: list[str], changes: list[tuple[date, str, str]]):
        self.current = sorted(set(current))
        self.changes = sorted(changes, key=lambda c: c[0], reverse=True)

    def at(self, d: date) -> list[str]:
        members = set(self.current)
        for when, added, removed in self.changes:
            if when <= d:
                break
            members.discard(added)
            if removed:
                members.add(removed)
        return sorted(members)

    def ever(self, start: date) -> list[str]:
        names = set(self.current) | set(self.at(start))
        for when, added, removed in self.changes:
            if when > start:
                names |= {s for s in (added, removed) if s}
        return sorted(names)

    def to_json(self) -> dict:
        return {"current": self.current, "changes": [[str(d), a, r] for d, a, r in self.changes]}

    @classmethod
    def from_json(cls, raw: dict) -> Membership:
        return cls(raw["current"], [(date.fromisoformat(d), a, r) for d, a, r in raw["changes"]])


def _page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "swingbot/0.1 (paper-trading research)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_membership() -> Membership:
    """Today's members from the list page; the change table from its own page (moved there in 2026)."""
    return Membership(parse_sp500(_page(SP500_URL)), parse_changes(_page(CHANGES_URL)))


# --- the frozen panel ------------------------------------------------------------------------


def _csv(daily: pd.DataFrame) -> str:
    df = daily[sorted(daily.columns)].sort_index()
    df.index = df.index.strftime("%Y-%m-%d")
    df.index.name = "date"
    return df.to_csv(float_format="%.6f")


def write_panel(daily: pd.DataFrame, out: Path, meta: dict) -> dict:
    text = _csv(daily)
    out.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode())
    (out / "closes.csv.gz").write_bytes(buf.getvalue())
    manifest = {**meta, "sha256": hashlib.sha256(text.encode()).hexdigest(), "symbols": daily.shape[1],
                "rows": daily.shape[0], "first": str(daily.index.min().date()), "last": str(daily.index.max().date())}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


def load_panel(path: Path) -> tuple[pd.DataFrame, dict]:
    manifest = json.loads((path / "manifest.json").read_text())
    try:
        text = gzip.decompress((path / "closes.csv.gz").read_bytes()).decode()
    except (OSError, EOFError) as e:
        raise ValueError(f"panel {path} is unreadable, so its hash cannot match the manifest") from e
    if hashlib.sha256(text.encode()).hexdigest() != manifest["sha256"]:
        raise ValueError(f"panel {path} does not match its manifest hash; rebuild it")
    df = pd.read_csv(io.StringIO(text), index_col="date", parse_dates=["date"])
    df.index.name = None
    return df, manifest


# --- benchmarks and statistics -----------------------------------------------------------------


def ew_returns(m: pd.DataFrame, members) -> pd.Series:
    """Equal weight in every member with a close at both month-ends, rebalanced monthly, no cost."""
    out = {}
    for i in range(len(m) - 1):
        names = [s for s in members(m.index[i]) if s in m.columns]
        a, b = m.iloc[i][names], m.iloc[i + 1][names]
        ok = a.notna() & b.notna() & (a > 0)
        out[m.index[i]] = float((b[ok] / a[ok] - 1).mean()) if ok.any() else float("nan")
    return pd.Series(out)


def trend_gate(spy: pd.Series, months: int = 10):
    def gate(d) -> bool:
        i = spy.index.get_loc(d)
        window = spy.iloc[max(0, i - months + 1):i + 1]
        return bool(i >= months - 1 and window.notna().all() and spy.iloc[i] > window.mean())
    return gate


def max_drawdown(rets: pd.Series) -> float:
    curve = (1 + rets).cumprod().to_numpy()
    if not len(curve):
        return 0.0
    peak = pd.Series([1.0, *curve]).cummax().to_numpy()[1:]
    return float((curve / peak - 1).min())


def summary(rets: pd.Series, base: pd.Series | None = None) -> dict:
    rets = rets.dropna()
    growth = float((1 + rets).prod())
    sd = float(rets.std())
    out = {"months": len(rets), "total": growth - 1, "cagr": growth ** (12 / len(rets)) - 1 if len(rets) else 0.0,
           "vol": sd * math.sqrt(12), "sharpe": float(rets.mean()) / sd * math.sqrt(12) if sd else 0.0,
           "max_dd": max_drawdown(rets)}
    if base is not None:
        diff = (rets - base.reindex(rets.index)).dropna()
        dsd = float(diff.std())
        out["t_vs_base"] = float(diff.mean()) / dsd * math.sqrt(len(diff)) if dsd else 0.0
    return out


# --- the study ------------------------------------------------------------------------------------


def _series(rows: list[dict]) -> pd.Series:
    return pd.Series({pd.Timestamp(r["held_to"]): r["ret"] for r in rows})


def run_study(daily: pd.DataFrame, membership: Membership) -> dict:
    m = month_ends(daily)

    def members(d):
        return membership.at(d.date())

    first, last = m.index[12], m.index[-2]
    gate = trend_gate(m["SPY"])
    runs = {}
    for cost in COSTS:
        runs[("M12", cost)] = replay(m, members, first, last, cost=cost, lookback=12)
        runs[("M6", cost)] = replay(m, members, first, last, cost=cost, lookback=6)
        runs[("TREND", cost)] = replay(m, ["SPY"], first, last, n=1, cost=cost, gate=gate)
        runs[("M12+TREND", cost)] = replay(m, members, first, last, cost=cost, lookback=12, gate=gate)
    held = [m.index[i + 1] for i in range(m.index.get_loc(first), m.index.get_loc(last) + 1)]
    ew = ew_returns(m, members)
    ew = pd.Series(ew.loc[first:last].to_numpy(), index=held)
    base = runs[("M12", COSTS[0])]
    bench = {b: pd.Series({pd.Timestamp(r["held_to"]): r["bench"].get(b, float("nan")) for r in base})
             for b in BENCH}
    series = {name: _series(runs[(name, COSTS[0])]) for name in STRATEGIES}
    series |= {"EW": ew, **bench}
    whole = {name: summary(s, ew) for name, s in series.items()}
    for name in STRATEGIES:
        rows = runs[(name, COSTS[0])]
        years = len(rows) / 12
        whole[name] |= {"turnover_yr": sum(r["turnover"] for r in rows) / years,
                        "cost_yr": sum(r["cost"] for r in rows) / years,
                        "cagr_stress": summary(_series(runs[(name, COSTS[1])]))["cagr"]}
    yearly = {}
    for name, s in series.items():
        yearly[name] = {}
        for y in sorted(set(s.index.year)):
            part = s[s.index.year == y].dropna()
            if len(part):
                yearly[name][str(y)] = {"ret": float((1 + part).prod() - 1), "max_dd": max_drawdown(part)}
    flat = sorted({sym for r in base for sym in r["flat"]})
    no_data = sorted(set(membership.ever(first.date())) - set(daily.columns))
    return {"first_held": str(held[0].date()), "last_held": str(held[-1].date()), "whole": whole,
            "yearly": yearly, "answers": answers(whole), "flat": flat,
            "flat_months": sum(len(r["flat"]) for r in base), "no_data": no_data}


def answers(w: dict) -> dict:
    m12, ew, m6 = w["M12"], w["EW"], w["M6"]
    if m12["cagr"] > ew["cagr"] and m12["t_vs_base"] >= 2:
        q1 = "support"
    elif m12["cagr"] > ew["cagr"]:
        q1 = "consistent, not shown"
    else:
        q1 = "did not add value"

    def helps(filtered: dict, plain: dict) -> bool:
        return abs(filtered["max_dd"]) <= abs(plain["max_dd"]) * 2 / 3 and plain["cagr"] - filtered["cagr"] < 0.02

    return {"q1_m12_vs_ew": q1,
            "q2_m6_vs_m12": "M6 higher" if m6["cagr"] > m12["cagr"] else "M12 higher",
            "q3_trend_vs_spy": "helps" if helps(w["TREND"], w["SPY"]) else "does not help",
            "q3_m12_trend_vs_m12": "helps" if helps(w["M12+TREND"], m12) else "does not help"}


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.1f}%"


def report(res: dict, manifest: dict) -> str:
    w, names = res["whole"], [*STRATEGIES, "EW", *BENCH]
    lines = ["# US history study (phase 1)", "",
             f"Held {res['first_held']} to {res['last_held']}. Panel: {manifest['symbols']} symbols, "
             f"{manifest['first']} to {manifest['last']}, feed {manifest.get('feed')}, fetched "
             f"{manifest.get('fetched_utc')}, sha256 `{manifest['sha256'][:16]}`. Rules: "
             "docs/research/us-research-prereg.md.", "",
             "## Whole window (0.05% a side)", "",
             "| | CAGR | CAGR at 0.15% | vol | Sharpe | worst fall | total | turnover/yr | cost/yr | t vs EW |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for n in names:
        s = w[n]
        stress = _pct(s["cagr_stress"]) if "cagr_stress" in s else ""
        trade = (f"{s['turnover_yr']:.1f}x | {_pct(s['cost_yr'])}" if "turnover_yr" in s else " | ")
        lines.append(f"| {n} | {_pct(s['cagr'])} | {stress} | {_pct(s['vol'])} | {s['sharpe']:.2f} | "
                     f"{_pct(s['max_dd'])} | {_pct(s['total'])} | {trade} | {s.get('t_vs_base', 0.0):.2f} |")
    years = sorted(res["yearly"]["M12"])
    lines += ["", "## By calendar year: return (worst fall in the year)", "",
              "| year | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for y in years:
        cells = [f"{_pct(v['ret'])} ({_pct(v['max_dd'])})" if (v := res["yearly"][n].get(y)) else "n/a"
                 for n in names]
        lines.append(f"| {y} | " + " | ".join(cells) + " |")
    a = res["answers"]
    lines += ["", "## Answers, read as pre-registered", "",
              f"1. M12 against EW: **{a['q1_m12_vs_ew']}** (CAGR {_pct(w['M12']['cagr'])} against "
              f"{_pct(w['EW']['cagr'])}, t = {w['M12']['t_vs_base']:.2f}). The trial continues either way.",
              f"2. M6 against M12: {a['q2_m6_vs_m12']} ({_pct(w['M6']['cagr'])} against "
              f"{_pct(w['M12']['cagr'])}). This changes nothing now.",
              f"3. TREND against SPY: **{a['q3_trend_vs_spy']}**; M12+TREND against M12: "
              f"**{a['q3_m12_trend_vs_m12']}** (needs a worst fall a third smaller, and under 2 points of "
              "CAGR given up).", "",
              "## Data gaps", "",
              f"- Members with no Alpaca data at all ({len(res['no_data'])}): {', '.join(res['no_data']) or 'none'}.",
              f"- M12 holdings counted flat for a month with no next close: {res['flat_months']} "
              f"({', '.join(res['flat']) or 'none'}).",
              "- Cash earns 0%; ticker reuse in the change table is not corrected; this is one ten-year path."]
    return "\n".join(lines) + "\n"


# --- CLI ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swingbot-us-research", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("panel")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None, help="default: the last month-end before today")
    p.add_argument("--out", type=Path, default=Path("data/raw/us_panel"))
    p = sub.add_parser("study")
    p.add_argument("--panel", type=Path, default=Path("data/raw/us_panel"))
    p.add_argument("--out", type=Path, default=Path("data/state/markets/us/research"))
    args = ap.parse_args(argv)
    try:
        return _panel(args) if args.cmd == "panel" else _study(args)
    except (AlpacaError, ValueError, OSError, KeyError) as e:
        print(f"swingbot-us-research: {e}", file=sys.stderr)
        return 1


def _panel(args) -> int:
    now = datetime.now(UTC)
    end = date.fromisoformat(args.end) if args.end else (pd.Period(now.date(), "M") - 1).end_time.date()
    start = date.fromisoformat(args.start)
    membership = fetch_membership()
    data = AlpacaData.from_env()
    symbols = sorted(set(membership.ever(start)) | set(BENCH))
    daily = data.daily_closes(symbols, start, end).dropna(axis=1, how="all")
    meta = {"source": "alpaca", "feed": data.feed, "adjustment": "all", "start": str(start), "end": str(end),
            "fetched_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "requested": len(symbols),
            "missing": sorted(set(symbols) - set(daily.columns)), "notes": data.notes}
    manifest = write_panel(daily, args.out, meta)
    (args.out / "membership.json").write_text(json.dumps(membership.to_json()) + "\n")
    print(json.dumps({k: manifest[k] for k in ("symbols", "requested", "rows", "first", "last", "sha256", "feed")}))
    return 0


def _study(args) -> int:
    daily, manifest = load_panel(args.panel)
    membership = Membership.from_json(json.loads((args.panel / "membership.json").read_text()))
    res = run_study(daily, membership)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "study.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    (args.out / "panel-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    (args.out / "membership.json").write_text((args.panel / "membership.json").read_text())
    text = report(res, manifest)
    (args.out / "study.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
