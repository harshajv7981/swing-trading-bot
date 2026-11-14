"""Mid-cap momentum paper trial (docs/research/momentum-paper-trial.md).

The rule is the one that passed docs/research/momentum-broad-prereg.md, run
forward on paper with no model calls:

* At each month's last NSE session, rank every stock with 260+ sessions of
  history and a 60-session median traded value of at least Rs 5 crore by that
  traded value. Ranks 101-250 are the mid-cap bucket.
* Hold the 20 of those with the best return from 6 month-ends back to 1
  month-end back, equal weight, until the next month's last session.

    swingbot-momentum update --data data/raw/eod2_data/daily [--pull]
    swingbot-momentum report
    swingbot-momentum scorecard [--open]   # HTML scorecard (src/swingbot/scorecard.py)

`update` catches up: every month-end since the trial's start that the data
already covers and the ledger lacks is booked at that day's closes, using only
prices up to that day. It is safe to run every day; it does nothing until a
month is complete in the data (eod2_data syncs about weekly). Past months are
never recomputed: picks, fills and share counts stay as booked.

Whole shares, Rs 10 lakh to start, 0.20% a side on the value traded (the
study's 0.40% round trip). Dividends are left out, as in the study. A split or
bonus shows up as the file's back-adjusted close on the booked day differing
from the booked price, and the share count is scaled by it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pandas as pd

from .risk.calendar import NSECalendar

LEDGER = Path("data/state/momentum_trial.json")
PICKS = Path("data/state/momentum_picks.json")   # same-day picks from the bhavcopy (`picks`)
REPORT = Path("runs/momentum-trial/report.md")
START = "2026-10-30"  # first booking: the portfolio held through November 2026
CAPITAL = 1_000_000.0
COST_SIDE = 0.002
TOP = 20
RANKS = (100, 250)
MIN_VALUE = 5e7  # Rs 5 crore
MIN_HISTORY = 260
LOOKBACK = 6
STALE_DAYS = 7  # eod2_data has synced as rarely as weekly; longer than that means something broke
# Index series, ETFs and debt funds share the folder with the stocks.
NOT_STOCK = re.compile(r"\s|etf|bees$|sensex|nifty|liquid|gilt|gsec")
INDEXES = {"Nifty Midcap 150": "nifty midcap 150", "Nifty Midcap150 Momentum 50": "nifty midcap150 momentum 50",
           "Gold ETF (GOLDBEES)": "goldbees"}
# Shadow ledger from docs/research/momentum-gold-prereg.md: the paper portfolio held as 70%
# of the money next to 30% in GOLDBEES, back to 70/30 at each December booking.
GOLD = "Gold ETF (GOLDBEES)"
GOLD_SHARE = 0.30
GOLD_COST = 0.001  # per side on gold moved; no STT on gold ETFs


def adjust_jumps(c: pd.Series) -> pd.Series:
    """Back-adjust one-day moves the files leave unadjusted (bonuses, splits, the ETF splits of Dec 2019).

    NSE price bands cap most stocks at 20% a day, so a close-to-close fall of more
    than 40% is taken as a corporate action (bonus, split or demerger): every
    earlier price is scaled by that day's ratio. Big rises are left alone: they
    are mostly gaps where the stock traded outside the EQ series.
    """
    ratio = c / c.shift()
    jumps = ratio[ratio < 0.6]
    if jumps.empty:
        return c
    factor = pd.Series(1.0, index=c.index)
    for day, r in jumps.items():
        factor[factor.index < day] *= r
    return c * factor


def load(data: Path, since: str = "2008-01-01",
         extra: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """EQ-series closes (jump-adjusted) and traded values for every stock file in `data`.

    `extra` (bhavcopy rows: Date, Symbol, Series, Close, Volume) fills the days
    after each file's last one, so a month-end can be ranked before eod2_data
    has synced it. Days the file already has are never overwritten."""
    by_sym = dict(tuple(extra.groupby("Symbol"))) if extra is not None and len(extra) else {}
    closes, values = {}, {}
    for p in sorted(data.glob("*.csv")):
        if NOT_STOCK.search(p.stem):
            continue
        try:
            df = pd.read_csv(p, usecols=["Date", "Close", "Volume", "Series"], parse_dates=["Date"],
                             index_col="Date")
        except ValueError:
            continue
        add = by_sym.get(p.stem.upper())
        if add is not None and len(df):
            add = add[add["Date"] > df.index.max()].set_index("Date")[["Close", "Volume", "Series"]]
            df = pd.concat([df, add]) if len(add) else df
        df = df[df["Series"].fillna("EQ") == "EQ"]
        df = df[~df.index.duplicated()].sort_index()
        df = df[df.index >= since]
        if len(df) < MIN_HISTORY:
            continue
        closes[p.stem.upper()] = adjust_jumps(df["Close"].astype(float))
        values[p.stem.upper()] = df["Close"].astype(float) * df["Volume"].astype(float)
    return pd.DataFrame(closes), pd.DataFrame(values)


def month_end_session(year: int, month: int, calendar: NSECalendar) -> date:
    day = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    while not calendar.is_session(day):
        day -= timedelta(days=1)
    return day


def select(px: pd.DataFrame, val: pd.DataFrame, asof: pd.Timestamp) -> tuple[list[str], list[str]]:
    """(the mid-cap bucket, the 20 picks) from data up to `asof`, as momentum_broad_study builds them."""
    after = px.index[px.index > asof]
    if asof not in px.index or (len(after) and after[0].to_period("M") == asof.to_period("M")):
        raise ValueError(f"{asof.date()} is not the last session of its month in the data")
    px, val = px[px.index <= asof], val[val.index <= asof]
    me_dates = px.groupby(px.index.to_period("M")).tail(1).index
    me_px = px.ffill(limit=5).loc[me_dates]
    hist = px.notna().cumsum().loc[asof]
    medval = val.rolling(60, min_periods=40).median().loc[asof]
    ok = (hist >= MIN_HISTORY) & (medval >= MIN_VALUE) & me_px.loc[asof].notna()
    bucket = medval[ok].sort_values(ascending=False).index[RANKS[0]:RANKS[1]]
    i = len(me_dates) - 1
    score = (me_px.iloc[i - 1] / me_px.iloc[i - LOOKBACK] - 1).reindex(bucket).dropna()
    return list(bucket), list(score.nlargest(TOP).index)


class Closes:
    """Closes for trading and valuing holdings: every series, not just EQ.

    The ranking uses EQ only, as the study did. A stock moved to the BE series
    (trade-for-trade) can still be sold, so holdings are priced from whatever
    series traded that day, jump-adjusted the same way.
    """

    def __init__(self, data: Path):
        self.data, self.cache = data, {}

    def series(self, sym: str) -> pd.Series:
        if sym not in self.cache:
            p = self.data / f"{sym.lower()}.csv"
            s = pd.Series(dtype=float)
            if p.exists():
                df = pd.read_csv(p, usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")
                s = df["Close"].astype(float)
                s = adjust_jumps(s[~s.index.duplicated()].sort_index().dropna())
            self.cache[sym] = s
        return self.cache[sym]

    def on(self, sym: str, asof: pd.Timestamp) -> tuple[float | None, bool]:
        """The close on `asof`, else the last one before it (flagged stale), else None."""
        s = self.series(sym)
        s = s[s.index <= asof]
        if s.empty:
            return None, True
        return float(s.iloc[-1]), s.index[-1] != asof


def split_ratio(r: float) -> float:
    """New shares per old share. adjust_jumps folds that day's own move into the ratio, so a
    2-for-1 split shows up as 1.99 or 2.02; a ratio within 3% of a simple fraction (up to
    tenths) is taken as that fraction. Anything else (a demerger) is kept as it is."""
    f = Fraction(r).limit_denominator(10)
    return float(f) if abs(r / float(f) - 1) < 0.03 else r


def book(ledger: dict, px: pd.DataFrame, val: pd.DataFrame, prices: Closes, asof: pd.Timestamp,
         indexes: dict[str, float]) -> dict:
    """Mark the holdings to `asof`'s closes, rebalance into the new picks, and append the month."""
    prev = ledger["months"][-1] if ledger["months"] else None
    bucket, picks = select(px, val, asof)
    notes, holdings, value = [], {}, ledger["capital"]
    if prev:
        value = prev["cash"]
        for sym, h in prev["holdings"].items():
            shares = h["shares"]
            then, _ = prices.on(sym, pd.Timestamp(prev["date"]))
            if then and abs(then / h["price"] - 1) > 0.01:  # split or bonus since booking
                shares = shares * split_ratio(h["price"] / then)
                notes.append(f"{sym}: back-adjusted close on {prev['date']} is {then:.2f}, booked "
                             f"{h['price']:.2f}; shares scaled to {shares:.2f}")
            now, stale = prices.on(sym, asof)
            if now is None:
                now, stale = h["price"], True
            if stale:
                notes.append(f"{sym}: no close on {asof.date()}; valued at the last one and kept")
            holdings[sym] = {"shares": shares, "price": now, "stale": stale}
        value += sum(h["shares"] * h["price"] for h in holdings.values())
    keep = {s: h for s, h in holdings.items() if h["stale"]}  # cannot trade without a price that day
    # Leave room for the costs, so cash never goes below zero.
    target = (value - sum(h["shares"] * h["price"] for h in keep.values())) * (1 - COST_SIDE) / TOP
    new, traded, orders = dict(keep), 0.0, []
    for sym in picks:
        if sym in keep:
            continue
        p, stale = prices.on(sym, asof)
        if stale:
            notes.append(f"{sym}: no close on {asof.date()}; bought at the last one")
        shares = float(int(target // p))
        new[sym] = {"shares": shares, "price": p}
    for sym in sorted(set(holdings) | set(new)):
        before = holdings.get(sym, {}).get("shares", 0.0)
        after = new.get(sym, {}).get("shares", 0.0)
        if after != before:
            p = new.get(sym, holdings.get(sym))["price"]
            traded += abs(after - before) * p
            orders.append({"symbol": sym, "side": "buy" if after > before else "sell",
                           "shares": abs(after - before), "price": p})
    costs = traded * COST_SIDE
    cash = value - sum(h["shares"] * h["price"] for h in new.values()) - costs
    month = {"date": str(asof.date()), "value": value, "costs": costs, "cash": cash, "turnover": traded / value / 2,
             "holdings": {s: {"shares": h["shares"], "price": h["price"]} for s, h in new.items()},
             "picks": picks, "bucket_size": len(bucket), "orders": orders, "notes": notes, "indexes": indexes}
    month["ew"] = _ew(prices, prev, bucket, asof)
    ledger["months"].append(month)
    return month


def _ew(prices: Closes, prev: dict | None, bucket: list[str], asof: pd.Timestamp) -> dict:
    """The study's baseline: equal weight in the whole bucket, after the same costs on what changes."""
    if not prev:
        return {"level": 1 - COST_SIDE, "names": bucket}
    old = prev["ew"]["names"]
    r = []
    for sym in old:
        then, _ = prices.on(sym, pd.Timestamp(prev["date"]))
        now, _ = prices.on(sym, asof)
        r.append(now / then - 1 if then and now else 0.0)
    gross = sum(r) / len(old) if old else 0.0
    turn = 0.5 * len(set(old) ^ set(bucket)) / max(len(bucket), 1)
    return {"level": prev["ew"]["level"] * (1 + gross - 2 * COST_SIDE * turn), "names": bucket}


def index_closes(data: Path, asof: pd.Timestamp) -> dict[str, float]:
    out = {}
    for name, stem in INDEXES.items():
        p = data / f"{stem}.csv"
        if p.exists():
            s = pd.read_csv(p, usecols=["Date", "Close"], parse_dates=["Date"], index_col="Date")["Close"]
            s = s[s.index <= asof].dropna()
            if not s.empty:
                out[name] = float(s.iloc[-1])
    return out


def new_ledger(start: str = START) -> dict:
    return {"start": start, "capital": CAPITAL, "cost_side": COST_SIDE, "months": []}


def due(ledger: dict, last_data: date, calendar: NSECalendar) -> list[tuple[int, int]]:
    """Months from the start that the data has finished and the ledger has not booked.

    A month is finished once the data reaches into a later month, or reaches the
    month's last session by the holiday list (when that year is in it).
    """
    booked = {m["date"][:7] for m in ledger["months"]}
    start = date.fromisoformat(ledger["start"])
    out, y, m = [], start.year, start.month
    while (y, m) <= (last_data.year, last_data.month):
        later = (last_data.year, last_data.month) > (y, m)
        if (later or (calendar.knows(y) and last_data >= month_end_session(y, m, calendar))) \
                and f"{y}-{m:02d}" not in booked:
            out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def last_date(data: Path) -> date:
    """The newest day in any stock file, read from each file's last line."""
    last = date.min
    for p in data.glob("*.csv"):
        if NOT_STOCK.search(p.stem):
            continue
        with p.open("rb") as f:
            f.seek(max(f.seek(0, 2) - 300, 0))
            tail = f.read().decode(errors="replace").strip().splitlines()
        try:
            last = max(last, date.fromisoformat(tail[-1].split(",")[0]))
        except (IndexError, ValueError):
            continue
    return last


def update(data: Path, ledger_path: Path = LEDGER, calendar: NSECalendar | None = None,
           start: str = START) -> list[dict]:
    calendar = calendar or NSECalendar.load()
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else new_ledger(start)
    last = last_date(data)
    todo, done = due(ledger, last, calendar), []
    if todo:
        y, m = todo[0]
        px, val = load(data, since=str(date(y, m, 1) - timedelta(days=800)))
        prices = Closes(data)
    for y, m in todo:
        days = px.index[(px.index.year == y) & (px.index.month == m)]
        asof = days.max()
        expected = month_end_session(y, m, calendar) if calendar.knows(y) else None
        done.append(book(ledger, px, val, prices, asof, index_closes(data, asof)))
        if expected and asof.date() != expected:
            done[-1]["notes"].append(f"the data's last day in the month is {asof.date()}, "
                                     f"not {expected} as the holiday list has it")
    filled = [m for m in ledger["months"] if "shortfall" not in m and date.fromisoformat(m["date"]) < last]
    for m in filled:
        m["shortfall"] = shortfall(data, m)
    if not (done or filled):
        return []
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1))
    tmp.replace(ledger_path)
    return done


def shortfall(data: Path, month: dict) -> dict:
    """What filling at the next session would have cost against the booked close, weighted by order value.

    Positive is a cost: buys paying more, sells getting less. `open` is the next session's open, `typical` its
    (high + low + close) / 3 as a stand-in for VWAP. Both come from the raw file, so the ratio is unaffected
    by later split adjustments. Orders without a close on the day or a next row are left out."""
    asof, tot, cost = pd.Timestamp(month["date"]), 0.0, {"open": 0.0, "typical": 0.0}
    for o in month["orders"]:
        p = data / f"{o['symbol'].lower()}.csv"
        df = pd.read_csv(p, parse_dates=["Date"], index_col="Date") if p.exists() else pd.DataFrame()
        if not {"Open", "High", "Low", "Close"} <= set(df.columns):
            continue
        df = df[~df.index.duplicated()].sort_index()
        after = df[df.index > asof].dropna(subset=["Open", "High", "Low", "Close"])
        if asof not in df.index or after.empty or not df.at[asof, "Close"] > 0:
            continue
        close, nxt = df.at[asof, "Close"], after.iloc[0]
        w = o["shares"] * o["price"] * (1 if o["side"] == "buy" else -1)
        cost["open"] += w * (nxt["Open"] / close - 1)
        cost["typical"] += w * ((nxt["High"] + nxt["Low"] + nxt["Close"]) / 3 / close - 1)
        tot += abs(w)
    if not tot:
        return {"open": None, "typical": None, "value": 0.0}
    return {"open": cost["open"] / tot, "typical": cost["typical"] / tot, "value": tot}


def record_memory(ledger_path: Path) -> str:
    """Step 7: turn the booked months into NSE episodes, in the same state folder as the ledger.

    Runs after a booking and never fails it: any problem is reported as one line.
    """
    try:
        from .markets.memory import Memory
        from .markets.trials import from_nse_ledger
        rows = from_nse_ledger(json.loads(Path(ledger_path).read_text()))
        n = Memory(Path(ledger_path).parent).update("nse", rows)
        return f"memory: {n} new episode records from {len(rows)} booked months"
    except Exception as e:      # noqa: BLE001 - memory must never stop the trial's booking
        return f"memory not updated: {e}"


def gold_mix(ledger: dict) -> list[float | None]:
    """Value of the 70/30 gold shadow at each booked month, as a multiple of the starting capital.

    None from the first month that has no gold close (data missing, or booked before
    the shadow existed)."""
    out, eq, gold = [], 0.0, 0.0
    for i, m in enumerate(ledger["months"]):
        g = m["indexes"].get(GOLD)
        if g is None or (out and out[-1] is None):
            out.append(None)
            continue
        if i == 0:
            eq = (1 - GOLD_SHARE) * m["value"] / ledger["capital"]
            gold = GOLD_SHARE * (1 - GOLD_COST)
        else:
            prev = ledger["months"][i - 1]
            eq *= m["value"] / prev["value"]
            gold *= g / prev["indexes"][GOLD]
            if m["date"][5:7] == "12":
                total = eq + gold
                moved = abs(gold - GOLD_SHARE * total)
                total -= moved * (COST_SIDE + GOLD_COST)
                eq, gold = (1 - GOLD_SHARE) * total, GOLD_SHARE * total
        out.append(eq + gold)
    return out


def report(ledger: dict) -> str:
    months = ledger["months"]
    if not months:
        return f"No month booked yet: the first is the close of {ledger['start']}.\n"
    first = months[0]
    lines = ["# Mid-cap momentum paper trial", "",
             f"Started {first['date']} with Rs {ledger['capital']:,.0f}. Rule: docs/research/momentum-paper-trial.md.", "",
             "| Month-end | Paper value | Month | Since start | Mid-cap EW since start | "
             + " | ".join(f"{n} since start" for n in INDEXES) + " | 70/30 with gold since start | Turnover | Costs |",
             "|---|---|---|---|---|" + "---|" * len(INDEXES) + "---|---|---|"]
    shadow = gold_mix(ledger)
    peak, worst = 0.0, 0.0
    for i, m in enumerate(months):
        prev = months[i - 1]["value"] if i else ledger["capital"]
        peak = max(peak, m["value"])
        worst = min(worst, m["value"] / peak - 1)
        idx = []
        for n in INDEXES:
            a, b = first["indexes"].get(n), m["indexes"].get(n)
            idx.append(f"{b / a - 1:+.1%}" if a and b else "n/a")
        mixed = f"{shadow[i] - 1:+.1%}" if shadow[i] is not None else "n/a"
        lines.append(f"| {m['date']} | Rs {m['value']:,.0f} | {m['value'] / prev - 1:+.1%} | "
                     f"{m['value'] / ledger['capital'] - 1:+.1%} | {m['ew']['level'] - 1:+.1%} | "
                     + " | ".join(idx) + f" | {mixed} | {m['turnover']:.0%} | Rs {m['costs']:,.0f} |")
    last = months[-1]
    lines += ["", f"Worst fall from a peak so far: {worst:.1%}.", "",
              "## Fills at the next session instead of the close", "",
              "Positive is a cost per rupee traded, on top of the 0.20% a side already charged. Judged after "
              "three rebalances: an average above 0.25% means the backtest's costs were too low.", "",
              "| Month-end | Next open | Next typical price | Traded |", "|---|---|---|---|"]
    for m in months:
        s = m.get("shortfall")
        if not s or s["open"] is None:
            lines.append(f"| {m['date']} | pending | pending | |" if not s else f"| {m['date']} | n/a | n/a | |")
            continue
        lines.append(f"| {m['date']} | {s['open']:+.2%} | {s['typical']:+.2%} | Rs {s['value']:,.0f} |")
    lines += ["",
              f"## Holdings from {last['date']} ({len(last['holdings'])} stocks, cash Rs {last['cash']:,.0f})", "",
              "| Stock | Shares | Price | Value |", "|---|---|---|---|"]
    for sym, h in sorted(last["holdings"].items()):
        lines.append(f"| {sym} | {h['shares']:g} | {h['price']:,.2f} | Rs {h['shares'] * h['price']:,.0f} |")
    if last["orders"]:
        lines += ["", f"## Orders booked on {last['date']}", "", "| Stock | Side | Shares | Price |", "|---|---|---|---|"]
        lines += [f"| {o['symbol']} | {o['side']} | {o['shares']:g} | {o['price']:,.2f} |" for o in last["orders"]]
    if last["notes"]:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in last["notes"]]
    return "\n".join(lines) + "\n"


def same_day_picks(data: Path, bhav_dir: Path, calendar: NSECalendar, month: str | None = None,
                   picks_path: Path = PICKS, ledger_path: Path = LEDGER) -> dict:
    """The month-end's 20 picks from eod2_data plus NSE bhavcopies for the days it lacks.

    The trial's own rule (`select`), on the trial's own data where it has the
    days. Written into `picks_path` in the ledger's shape ({"months": [...]}),
    which `swingbot-dhan rebalance --picks` reads. The paper ledger is not
    touched: when eod2_data catches up, `update` books the month as before,
    and the picks here are compared with it."""
    from .bhavcopy import read_dir

    extra = read_dir(bhav_dir)
    eod2_last = last_date(data)
    newest = max([eod2_last] + ([extra["Date"].max().date()] if len(extra) else []))
    if month:
        y, m = (int(x) for x in month.split("-"))
        asof = month_end_session(y, m, calendar)
    else:
        asof = month_end_session(newest.year, newest.month, calendar)
        if asof > newest:
            prev = date(newest.year, newest.month, 1) - timedelta(days=1)
            asof = month_end_session(prev.year, prev.month, calendar)
    have = set(extra["Date"].dt.date) if len(extra) else set()
    if asof > eod2_last and asof not in have:
        raise ValueError(f"no bhavcopy for the month-end {asof} in {bhav_dir}, and eod2_data stops at {eod2_last}")
    missing, day = [], eod2_last
    while day < asof:
        day += timedelta(days=1)
        if calendar.is_session(day) and day not in have:
            missing.append(str(day))
    px, val = load(data, since=str(asof - timedelta(days=800)), extra=extra)
    bucket, picks = select(px, val, pd.Timestamp(asof))
    entry = {"date": str(asof), "picks": picks, "bucket_size": len(bucket), "source": "eod2_data + bhavcopy",
             "eod2_last": str(eod2_last), "missing_sessions": missing,
             "made_at": pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M")}
    book = json.loads(picks_path.read_text()) if picks_path.exists() else {"months": []}
    book["months"] = sorted([m for m in book["months"] if m["date"] != entry["date"]] + [entry],
                            key=lambda m: m["date"])
    picks_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = picks_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, indent=1))
    tmp.replace(picks_path)
    if ledger_path.exists():
        booked = next((m for m in json.loads(ledger_path.read_text())["months"] if m["date"] == entry["date"]), None)
        if booked:
            entry["trial_match"] = sorted(booked["picks"]) == sorted(picks)
            entry["trial_only"] = sorted(set(booked["picks"]) - set(picks))
            entry["here_only"] = sorted(set(picks) - set(booked["picks"]))
    return entry


def notify(text: str, run=subprocess.run) -> None:
    """Print, and on a Mac show a notification: the nightly launchd job otherwise fails only into its log."""
    print(text, file=sys.stderr)
    if shutil.which("osascript"):
        run(["osascript", "-e", f'display notification "{text}" with title "swingbot"'], check=False)


def pull(data: Path, tries: int = 3, run=subprocess.run, sleep=time.sleep) -> None:
    """Bring a shallow eod2_data clone up to date (the folder holding `daily/`).

    Retries network blips: the clone is blob-less, so the reset downloads the day's files and has timed out.
    The last failure also raises a macOS notification, since the nightly launchd job otherwise fails only
    into its log."""
    repo = str(data.parent)
    for attempt in range(tries):
        try:
            run(["git", "-C", repo, "fetch", "--depth", "1", "origin"], check=True)
            run(["git", "-C", repo, "reset", "--hard", "--quiet", "FETCH_HEAD"], check=True)
            return
        except subprocess.CalledProcessError as e:
            if attempt == tries - 1:
                notify("eod2_data pull failed: see ~/Library/Logs/swingbot/momentum.log", run)
                raise
            print(f"pull failed ({e}); retrying", file=sys.stderr)
            sleep(60 * (attempt + 1))


def main(argv: list[str] | None = None) -> int:
    from . import scorecard  # imported here: it imports this module
    ap = argparse.ArgumentParser(prog="swingbot-momentum", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update", help="book every completed month-end the ledger lacks")
    u.add_argument("--data", required=True, type=Path, help="eod2_data's daily/ folder")
    u.add_argument("--pull", action="store_true", help="git-update the eod2_data clone first")
    u.add_argument("--holidays", help="NSE holiday TOML (default config/nse_holidays.toml)")
    pk = sub.add_parser("picks", help="the month-end picks on the day, from eod2_data plus NSE bhavcopies")
    pk.add_argument("--data", required=True, type=Path, help="eod2_data's daily/ folder")
    pk.add_argument("--bhavcopy", type=Path, default=Path("data/raw/bhavcopy"),
                    help="folder of NSE bhavcopy files (.csv or .zip) for the days eod2_data lacks")
    pk.add_argument("--month", help="YYYY-MM (default: the newest month-end the data reaches)")
    pk.add_argument("--fetch", action="store_true",
                    help="download the missing sessions' bhavcopies from NSE first (needs --accept-nse-terms)")
    pk.add_argument("--accept-nse-terms", action="store_true",
                    help="NSE's terms forbid automated collection; confirm you accept that for a private, "
                         "one-file-a-day download")
    pk.add_argument("--out", type=Path, default=PICKS)
    pk.add_argument("--holidays", help="NSE holiday TOML (default config/nse_holidays.toml)")
    sc = sub.add_parser("scorecard", help="write the HTML scorecard: measures, charts, review checks")
    sc.add_argument("--out", type=Path, default=Path(scorecard.OUT))
    sc.add_argument("--open", action="store_true", help="open the page in the browser")
    for p in (u, sub.add_parser("report", help="print the trial so far"), pk, sc):
        p.add_argument("--ledger", type=Path, default=LEDGER)
    args = ap.parse_args(argv)
    if args.cmd == "picks":
        return _picks(args)
    if args.cmd == "scorecard":
        out = _scorecard(args.ledger, args.out)
        print(f"wrote {out}")
        if args.open:
            webbrowser.open(out.resolve().as_uri())
        return 0
    if args.cmd == "update":
        if args.pull:  # the nightly job: also say when the data stops moving, since bookings wait for it
            pull(args.data)
            if (lag := (date.today() - last_date(args.data)).days) > STALE_DAYS:
                notify(f"eod2_data is {lag} days old: month-end bookings wait until it catches up")
        done = update(args.data, args.ledger, NSECalendar.load(args.holidays))
        if not done:
            print("nothing to book: no new month-end in the data yet")
            return 0
        for m in done:
            print(f"booked {m['date']}: value Rs {m['value']:,.0f}, {len(m['orders'])} orders, "
                  f"costs Rs {m['costs']:,.0f}")
        print(record_memory(args.ledger))
    ledger = json.loads(args.ledger.read_text()) if args.ledger.exists() else new_ledger()
    text = report(ledger)
    if args.cmd == "update":
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(text)
        print(f"report: {REPORT}, scorecard: {_scorecard(args.ledger, Path(scorecard.OUT))}")
    else:
        sys.stdout.write(text)
    return 0


def _scorecard(ledger_path: Path, out: Path) -> Path:
    from . import scorecard
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else new_ledger()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(scorecard.render(ledger))
    return out


def _picks(args) -> int:
    calendar = NSECalendar.load(args.holidays)
    if args.fetch:
        if not args.accept_nse_terms:
            print("error: --fetch downloads from NSE, whose terms forbid automated collection; download the "
                  "bhavcopy by hand, or pass --accept-nse-terms", file=sys.stderr)
            return 2
        from .bhavcopy import fetch
        day, until = last_date(args.data), date.today()
        if args.month:
            y, m = (int(x) for x in args.month.split("-"))
            until = min(until, month_end_session(y, m, calendar))
        while day < until:
            day += timedelta(days=1)
            if calendar.is_session(day):
                try:
                    print(f"fetched {fetch(day, args.bhavcopy)}")
                except Exception as e:   # a holiday the list lacks, or NSE not ready yet
                    print(f"no bhavcopy for {day}: {e}", file=sys.stderr)
    try:
        e = same_day_picks(args.data, args.bhavcopy, calendar, args.month, args.out, args.ledger)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"picks for the month-end {e['date']} (eod2_data to {e['eod2_last']}, bucket {e['bucket_size']}):")
    print("  " + ", ".join(e["picks"]))
    if e["missing_sessions"]:
        print(f"  warning: no data for {', '.join(e['missing_sessions'])}; the 60-day traded value that "
              "picks the mid-cap bucket skips those days, so a stock near rank 100 or 250 could differ")
    if "trial_match" in e:
        print("  the paper trial booked the same 20" if e["trial_match"] else
              f"  differs from the paper trial: trial only {e['trial_only']}, here only {e['here_only']}")
    print(f"saved to {args.out}; trade them with `swingbot-dhan rebalance --picks {args.out}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
