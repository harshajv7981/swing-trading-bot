"""The monthly adviser brief: the M6 rule's stock list, with the data behind each pick, to take to a
SEBI-registered adviser before any real money moves.

    swingbot-momentum brief --data data/raw/eod2_data/daily [--month 2026-10]

Written to runs/momentum-trial/brief-YYYY-MM.md (git-ignored, private). The nightly job writes a new one
after each month-end booking and sends a notification. The list is a rule's output, not a recommendation.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .momentum import LEDGER, LOOKBACK, load, select
from .risk.portfolio import load_sectors

OUT = Path("runs/momentum-trial")
YEAR = 250  # trading days

# The backtest and test results behind the rule, from docs/research (fixed numbers, not recomputed here).
EVIDENCE = """\
- **Backtest, Jan 2010 – Sep 2026, after costs:** the rule made +14.8% a year, or +12.0% after 20.8%
  short-term tax. Its worst fall was -59%. Nifty 50 buy-and-hold made +10.7% (+9.9% after tax), with a
  worst fall of -35% (book-rules-prereg.md).
- **Overfitting check:** 87 related versions were tried. The deflated Sharpe ratio is 0.78, short of the
  usual 0.95 bar, so the real edge is probably about half the backtest's (momentum-overfit-prereg.md).
- **Against the cheap alternative:** after tax, the rule trailed a Nifty Midcap150 Momentum 50 index fund
  over 2010–2026, but led it over the 49 months since that index launched. By the rule fixed beforehand,
  the fund wins (passive-twin-prereg.md).
- **Missing data:** companies that were later delisted are absent from the price history, which flatters
  a mid-cap rule. The size of that effect is unmeasured (survivorship-free-m6-prereg.md).
- **Turnover:** about half the list changes each month, so nearly every gain is short-term."""

QUESTIONS = """\
1. Does a 20-stock, equal-weight mid-cap list suit my risk profile and the money I can afford to lose?
2. Are any of these stocks under exchange surveillance (ASM/GSM), heavily pledged, or carrying governance
   or results concerns that price data can't show?
3. Is the list too concentrated in one sector, theme or business group?
4. Given about 50% monthly turnover and 20.8% short-term tax, is holding these directly better for me
   than a Midcap150 Momentum 50 index fund held over a year?
5. How much should go in, if anything, and how does it fit my written limits: a Rs 2.5 lakh cap, and
   stopping additions after a 25% fall?"""


def _month_end(ledger: dict, month: str | None) -> dict:
    months = ledger["months"]
    if not months:
        raise ValueError("no month booked yet")
    if month is None:
        return months[-1]
    m = next((m for m in months if m["date"].startswith(month)), None)
    if m is None:
        raise ValueError(f"no booking for {month}; booked: {', '.join(x['date'][:7] for x in months)}")
    return m


def rows(px: pd.DataFrame, val: pd.DataFrame, asof: pd.Timestamp, picks: list[str]) -> pd.DataFrame:
    """The numbers behind each pick, from data up to `asof` only."""
    px, val = px[px.index <= asof], val[val.index <= asof]
    me_px = px.ffill(limit=5).groupby(px.index.to_period("M")).tail(1)
    i = len(me_px) - 1
    score = me_px.iloc[i - 1] / me_px.iloc[i - LOOKBACK] - 1
    year = px.iloc[-YEAR:]
    daily = year.pct_change()
    out = pd.DataFrame(index=picks)
    out["score"] = score.reindex(picks)
    out["last_month"] = (me_px.iloc[i] / me_px.iloc[i - 1] - 1).reindex(picks)
    out["twelve_months"] = (me_px.iloc[i] / me_px.iloc[max(i - 12, 0)] - 1).reindex(picks)
    out["volatility"] = (daily.std() * math.sqrt(YEAR)).reindex(picks)
    out["worst_fall"] = (year / year.cummax() - 1).min().reindex(picks)
    out["traded_cr"] = (val.rolling(60, min_periods=40).median().iloc[-1] / 1e7).reindex(picks)
    out["close"] = px.iloc[-1].reindex(picks)
    return out.sort_values("score", ascending=False)


def streak(ledger: dict, upto: str, sym: str) -> int:
    """Consecutive month-ends, ending at `upto`, that held `sym`."""
    n = 0
    for m in reversed([m for m in ledger["months"] if m["date"] <= upto]):
        if sym not in m["picks"]:
            break
        n += 1
    return n


def brief(ledger: dict, px: pd.DataFrame, val: pd.DataFrame, month: dict, sectors: dict[str, str]) -> str:
    asof = pd.Timestamp(month["date"])
    bucket, picks = select(px, val, asof)
    if sorted(picks) != sorted(month["picks"]):
        raise ValueError(f"the data no longer gives the booked picks for {month['date']}; check booking notes")
    prev = [m for m in ledger["months"] if m["date"] < month["date"]]
    before = set(prev[-1]["picks"]) if prev else set()
    t = rows(px, val, asof, picks)
    lines = [
        f"# Monthly stock list for adviser review: {asof:%B %Y}",
        "",
        f"As of the close on {asof.date()}. **The output of a fixed rule, not a recommendation.** It is for "
        "discussion with a SEBI-registered investment adviser before any money is invested. Past results don't "
        "predict future ones.",
        "",
        "## The list",
        "",
        "Equal weight (about 5% each), held until the next month-end, then re-ranked.",
        "",
        "| # | Stock | Sector | 6-1 month return (the ranking) | Last month | 12 months | Volatility | "
        "Worst fall, past year | Traded value (Rs cr/day) | Close | In the list |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, (sym, r) in enumerate(t.iterrows(), 1):
        status = f"{streak(ledger, month['date'], sym)} months" if sym in before else "new"
        lines.append(f"| {k} | {sym} | {sectors.get(sym, '–')} | {r['score']:+.0%} | {r['last_month']:+.0%} | "
                     f"{r['twelve_months']:+.0%} | {r['volatility']:.0%} | {r['worst_fall']:.0%} | "
                     f"{r['traded_cr']:,.0f} | {r['close']:,.2f} | {status} |")
    added, dropped = sorted(set(picks) - before), sorted(before - set(picks))
    if prev:
        lines += ["", f"**Changes since {prev[-1]['date']}:** added {', '.join(added) or 'none'}; "
                      f"dropped {', '.join(dropped) or 'none'}."]
    lines += [
        "",
        "## How the list is made",
        "",
        f"- **Universe:** NSE stocks ranked 101–250 by 60-day median traded value (a stand-in for the "
        f"mid-cap list), with at least ₹5 crore traded a day and a year of history. {len(bucket)} qualified "
        "this month.",
        "- **Ranking:** each stock's return over the past 6 months, skipping the latest month.",
        "- **Selection:** the top 20, equal weight, rebalanced at each month-end.",
        "- **What it ignores:** price data only. No earnings, valuation, news or governance checks.",
        "",
        "## What the evidence says about trusting it",
        "",
        EVIDENCE,
    ]
    if ledger["months"]:
        first, last = ledger["months"][0], month
        lines += [f"- **Forward paper trial:** started {first['date']}. As of {last['date']}, "
                  f"{last['value'] / ledger['capital'] - 1:+.1%} since the start, against "
                  f"{last['ew']['level'] - 1:+.1%} for the equal-weight mid-cap bucket. Judged in October 2027."]
    lines += ["", "## Questions to ask the adviser", "", QUESTIONS, ""]
    return "\n".join(lines)


def write(data: Path, month: str | None = None, ledger_path: Path = LEDGER, out: Path = OUT) -> Path:
    ledger = json.loads(ledger_path.read_text())
    m = _month_end(ledger, month)
    asof = pd.Timestamp(m["date"])
    # The same window `update` loaded when it booked this month, so the history cut-off ranks the same.
    px, val = load(data, since=str((asof.date().replace(day=1) - timedelta(days=800))))
    path = out / f"brief-{m['date'][:7]}.md"
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(brief(ledger, px, val, m, load_sectors()))
    return path
