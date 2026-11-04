"""Scorecard for the mid-cap momentum paper trial: one HTML page from the ledger.

    swingbot-momentum scorecard            # writes runs/momentum-trial/scorecard.html
    swingbot-momentum scorecard --open     # and opens it

`swingbot-momentum update` writes it too, after each booking. It reads only the
ledger (`data/state/momentum_trial.json`) and fetches nothing.

Every series is a monthly level, 1.0 at the first booking: the paper account,
mid-cap EW, the three indexes and the 70/30 gold shadow. From those come the
usual measures (CAGR, volatility, Sharpe, Sortino, worst fall, worst month), the
paper account against mid-cap EW month by month, and the October 2027 checks
from docs/research/momentum-paper-trial.md that can be read off the ledger.

Sharpe and Sortino use a 0% risk-free rate and monthly returns times sqrt(12).
Under 12 months the CAGR is left blank and the page says the numbers are noise:
the backtest needed years to separate the rule from luck.
"""

from __future__ import annotations

import math

import pandas as pd

from .dashboard import CSS, _e, _table, _tile
from .momentum import INDEXES, gold_mix

OUT = "runs/momentum-trial/scorecard.html"
PAPER, EW, MIX = "Paper account (M6)", "Mid-cap EW", "70/30 with gold"
MIDCAP = "Nifty Midcap 150"
REVIEW_MONTHS = 12
COLORS = {PAPER: "var(--accent)", EW: "var(--bench)", MIDCAP: "#c2761b", "Nifty Midcap150 Momentum 50": "#7a52a8",
          "Gold ETF (GOLDBEES)": "#b8a02b", MIX: "var(--good)"}


def levels(ledger: dict) -> pd.DataFrame:
    """One column per series, one row per booked month-end, each 1.0 at the first booking.

    A series is NaN from the first month it lacks a value."""
    months = ledger["months"]
    idx = pd.to_datetime([m["date"] for m in months])
    cols = {PAPER: [m["value"] / ledger["capital"] for m in months], EW: [m["ew"]["level"] for m in months]}
    for n in INDEXES:
        a = months[0]["indexes"].get(n) if months else None
        cols[n] = [m["indexes"][n] / a if a and m["indexes"].get(n) else math.nan for m in months]
    cols[MIX] = [math.nan if v is None else v for v in gold_mix(ledger)]
    df = pd.DataFrame(cols, index=idx, dtype=float)
    return df.where(df.notna().cummin())  # a gap ends the series: no stitching across it


def stats(level: pd.Series) -> dict:
    lv = level.dropna()
    r = lv.pct_change().dropna()
    out = {"months": len(r), "total": lv.iloc[-1] / lv.iloc[0] - 1 if len(lv) > 1 else math.nan}
    out["cagr"] = (1 + out["total"]) ** (12 / len(r)) - 1 if len(r) >= REVIEW_MONTHS else math.nan
    sd = r.std() if len(r) > 2 else math.nan
    out["vol"] = sd * math.sqrt(12)
    out["sharpe"] = r.mean() / sd * math.sqrt(12) if sd and not math.isnan(sd) else math.nan
    dsd = math.sqrt((r.clip(upper=0) ** 2).mean()) if len(r) > 2 else math.nan
    out["sortino"] = r.mean() / dsd * math.sqrt(12) if dsd and not math.isnan(dsd) else math.nan
    out["max_dd"] = (lv / lv.cummax() - 1).min() if len(lv) else math.nan
    out["worst_month"] = r.min() if len(r) else math.nan
    out["up_share"] = (r > 0).mean() if len(r) else math.nan
    return out


def versus(paper: pd.Series, other: pd.Series) -> dict:
    """The paper account against one comparison, on the months both have."""
    both = pd.concat([paper, other], axis=1).dropna()
    r = both.pct_change().dropna()
    if r.empty:
        return {"months": 0, "beat": 0, "excess": math.nan, "te": math.nan, "ir": math.nan, "beta": math.nan}
    a = r.iloc[:, 0] - r.iloc[:, 1]
    te = a.std() * math.sqrt(12) if len(a) > 2 else math.nan
    var = r.iloc[:, 1].var() if len(r) > 2 else math.nan
    return {"months": len(r), "beat": int((a > 0).sum()),
            "excess": both.iloc[-1, 0] / both.iloc[0, 0] - both.iloc[-1, 1] / both.iloc[0, 1],
            "te": te, "ir": a.mean() * 12 / te if te and not math.isnan(te) else math.nan,
            "beta": r.iloc[:, 0].cov(r.iloc[:, 1]) / var if var and not math.isnan(var) else math.nan}


def checks(ledger: dict, lv: pd.DataFrame) -> list[tuple[str, str, str]]:
    """(check, status, detail) for the review rules the ledger can answer; the rest need Harsha."""
    n = max(len(ledger["months"]) - 1, 0)
    notes = sum(len(m["notes"]) for m in ledger["months"])
    ahead = versus(lv[PAPER], lv[EW])["excess"]
    return [
        ("Months held", f"{n} of {REVIEW_MONTHS}", "the review is at the booking at the end of Oct 2027"),
        ("1. Booked from the data, no hand fixes", "review notes" if notes else "ok so far",
         f"{notes} booking note(s) to read (splits, stale prices, date mismatches)" if notes else "no booking notes"),
        ("2. Beat mid-cap EW after costs", "–" if math.isnan(ahead) else ("ahead" if ahead > 0 else "behind"),
         "–" if math.isnan(ahead) else f"{ahead * 100:+.1f} points since the start"
         + ("" if n >= REVIEW_MONTHS else "; only the 12-month figure counts")),
        ("3. Written plan for a -60% fall", "Harsha", "not something the ledger can show"),
        ("4. Indian tax checked", "Harsha", "short-term gains at 20% vs 12.5% for an index fund held a year"),
    ]


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _pct(x, digits=1, signed=True) -> str:
    return "–" if _nan(x) else f"{x * 100:{'+' if signed else ''}.{digits}f}%"


def _pts(x) -> str:
    return "–" if _nan(x) else f"{x * 100:+.1f} pts"


def _num(x, digits=2) -> str:
    return "–" if _nan(x) else f"{x:.{digits}f}"


def _lines(df: pd.DataFrame, label: str, fmt, zero: float | None = None) -> str:
    """A small multi-line SVG chart; `fmt` labels the y grid."""
    df = df.dropna(how="all", axis=1)
    if len(df) < 2 or df.empty:
        return '<p class="empty">The chart appears after the second month-end.</p>'
    w, h, pad = 1000, 260, 52
    lo, hi = float(df.min().min()), float(df.max().max())
    if zero is not None:
        lo, hi = min(lo, zero), max(hi, zero)
    if hi == lo:
        hi, lo = hi + 0.01, lo - 0.01
    x = lambda i: pad + i * (w - pad - 8) / (len(df) - 1)
    y = lambda v: 8 + (hi - v) * (h - 30) / (hi - lo)
    grid = ""
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        grid += (f'<line x1="{pad}" x2="{w - 8}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--line)"/>'
                 f'<text x="{pad - 6}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" '
                 f'fill="var(--muted)">{fmt(v)}</text>')
    for i in sorted({0, len(df) // 2, len(df) - 1}):
        anchor = "start" if i == 0 else "end" if i == len(df) - 1 else "middle"
        grid += (f'<text x="{x(i):.1f}" y="{h - 4}" text-anchor="{anchor}" font-size="11" '
                 f'fill="var(--muted)">{df.index[i].date()}</text>')
    paths, legend = "", ""
    for col in reversed(df.columns):  # the paper account last, so it draws on top
        pts = [(x(i), y(v)) for i, v in enumerate(df[col]) if not math.isnan(v)]
        d = " ".join(f"{'M' if j == 0 else 'L'}{a:.1f},{b:.1f}" for j, (a, b) in enumerate(pts))
        width = 2.5 if col == PAPER else 1.5
        dash = "" if col in (PAPER, MIX) else ' stroke-dasharray="4 3"'
        paths += f'<path d="{d}" fill="none" stroke="{COLORS.get(col, "var(--muted)")}" stroke-width="{width}"{dash}/>'
    for col in df.columns:
        legend += f'<b style="background:{COLORS.get(col, "var(--muted)")}"></b>{_e(col)}'
    return (f'<div class="card"><svg viewBox="0 0 {w} {h}" role="img" aria-label="{_e(label)}">{grid}{paths}</svg>'
            f'<div class="legend">{legend}</div></div>')


def render(ledger: dict) -> str:
    months = ledger["months"]
    title = "Momentum paper trial scorecard"
    parts = [f"<h1>{title}</h1>"]
    if not months:
        parts.append(f'<p class="sub">No month booked yet: the first is the close of {_e(ledger["start"])}.</p>')
        return _page(title, parts)
    lv = levels(ledger)
    held = len(months) - 1
    p, e = stats(lv[PAPER]), versus(lv[PAPER], lv[EW])
    m150 = versus(lv[PAPER], lv[MIDCAP])
    last = months[-1]
    parts.append(f'<p class="sub">Started {_e(months[0]["date"])} with Rs {ledger["capital"]:,.0f}; last booked '
                 f'{_e(last["date"])}. Read-only: rebuilt from the ledger by <code>swingbot-momentum scorecard</code>.</p>')
    if held < REVIEW_MONTHS:
        parts.append(f'<p class="sub warn">{held} month(s) held. Under {REVIEW_MONTHS} months these numbers are '
                     "mostly noise; the trial checks that the rule behaves as tested, not that it works.</p>")
    parts.append('<div class="tiles">' + "".join([
        _tile("Paper value", f"Rs {last['value']:,.0f}", f"{_pct(p['total'])} since the start"),
        _tile("Vs mid-cap EW", _pts(e["excess"]), f"beat it in {e['beat']} of {e['months']} months"),
        _tile("Vs Nifty Midcap 150", _pts(m150["excess"]), f"beta {_num(m150['beta'])}"),
        _tile("Worst fall", _pct(p["max_dd"]), f"worst month {_pct(p['worst_month'])}"),
        _tile("Sharpe", _num(p["sharpe"]), f"Sortino {_num(p['sortino'])}, risk-free 0%"),
        _tile("Months to review", f"{held}/{REVIEW_MONTHS}", "Oct 2027 booking", bar=min(held / REVIEW_MONTHS, 1)),
    ]) + "</div>")
    parts += ["<h2>Growth of Rs 1 at the first booking</h2>", _lines(lv, "Growth", lambda v: f"{v:.2f}")]
    dd = lv[[PAPER, EW, MIDCAP]]
    parts += ["<h2>Fall from the peak</h2>",
              _lines(dd / dd.cummax() - 1, "Drawdown", lambda v: f"{v * 100:.0f}%", zero=0.0)]
    rows = []
    for col in lv.columns:
        s = stats(lv[col])
        if s["months"] == 0:
            continue
        rows.append([f"<b>{_e(col)}</b>" if col == PAPER else _e(col), s["months"], _pct(s["total"]),
                     _pct(s["cagr"]), _pct(s["vol"], signed=False), _num(s["sharpe"]), _num(s["sortino"]),
                     _pct(s["max_dd"]), _pct(s["worst_month"]), _pct(s["up_share"], 0, signed=False)])
    parts += ["<h2>Measures</h2>", _table(
        ["Series", "Months", "Total", "CAGR", "Volatility", "Sharpe", "Sortino", "Worst fall", "Worst month",
         "Months up"], rows, num=set(range(1, 10)), empty="No month held yet.")]
    if e["months"]:
        parts.append(f'<p class="sub">Against mid-cap EW: tracking error {_pct(e["te"], signed=False)} a year, '
                     f'information ratio {_num(e["ir"])}.</p>')
    tone = {"ok so far": "good", "ahead": "good", "behind": "warn", "review notes": "warn"}
    parts += ["<h2>October 2027 review</h2>", _table(
        ["Check", "Status", "Detail"],
        [[_e(c), f'<span class="{tone.get(s, "")}">{_e(s)}</span>',
          f'<span style="white-space:normal">{_e(d)}</span>'] for c, s, d in checks(ledger, lv)])]
    r = lv.pct_change().iloc[1:]
    cols = [PAPER, EW] + [c for c in r.columns if c not in (PAPER, EW)]
    parts += ["<h2>Month by month</h2>", _table(
        ["Month held to", *cols, "Turnover", "Costs"],
        [[_e(str(d.date())), *[_pct(r.at[d, c]) for c in cols],
          f"{months[i + 1]['turnover']:.0%}", f"Rs {months[i + 1]['costs']:,.0f}"]
         for i, d in reversed(list(enumerate(r.index)))],
        num=set(range(1, len(cols) + 3)), empty="No month held yet.")]
    return _page(title, parts)


def _page(title: str, parts: list[str]) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_e(title)}</title><style>{CSS}</style></head><body><main>"
            + "".join(parts) + "</main></body></html>\n")
