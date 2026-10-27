"""Market mood: is the Indian market leaning bullish, neutral or defensive?

A handful of plain checks on free daily prices, each read as bullish,
neutral or defensive, then counted into one verdict. The shape comes from the
macro snapshot in HKUDS/AI-Trader (README says MIT; github.com/HKUDS/AI-Trader),
which does the same for US markets with QQQ, XLP, GLD and UUP. The checks,
the Indian series and the code are this repo's own.

Every series is cut to the sessions that had closed by the as-of time
(`technicals.bars_known_at`), so a backtest at a past date never sees a later
close. A series that is missing or too short is reported as unavailable and
left out of the count; it is never read as neutral. With fewer than
`MIN_CHECKS` checks available, the verdict is "unknown".

The dollar-rupee rate is the exception to the 15:30 cut. It trades round the
clock, and Yahoo's daily FX bar for a date closes late that night, after Indian
markets shut. So a USD/INR bar counts as known only from 06:00 IST the next
day (`fx_bars_known_at`); news at 16:00 sees the day before's rate.

Series, by the name of their saved CSV (and the Yahoo symbol the data router
maps it to):

    NIFTY50    Nifty 50 index             ^NSEI
    NIFTYFMCG  Nifty FMCG index           ^CNXFMCG
    GOLDBEES   Nippon gold ETF, rupees    GOLDBEES.NS
    USDINR     dollar-rupee rate          INR=X
    INDIAVIX   India VIX                  ^INDIAVIX
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from .models import IST
from .technicals import bars_known_at

SERIES = {
    "nifty": "NIFTY50",
    "fmcg": "NIFTYFMCG",
    "gold": "GOLDBEES",
    "usdinr": "USDINR",
    "vix": "INDIAVIX",
}
LOOKBACK = 20      # sessions for the trend checks
TREND_SMA = 200    # the Nifty's long trend line
MIN_CHECKS = 3     # fewer available checks than this and the verdict is "unknown"
VERDICTS = ("bullish", "neutral", "defensive", "unknown")
FX_SERIES = {"usdinr"}   # bars whose close prints after the NSE close
FX_KNOWN_AFTER = pd.Timedelta(days=1, hours=6)  # a bar dated D is known from D+1 06:00 IST


def fx_bars_known_at(bars: pd.DataFrame, when) -> pd.DataFrame:
    """FX bars whose close had printed by `when`: a bar dated D from D+1 06:00 IST."""
    ts = pd.Timestamp(when).tz_convert(IST).tz_localize(None)
    return bars[bars.index + FX_KNOWN_AFTER <= ts]


@dataclass(frozen=True)
class MoodCheck:
    id: str
    label: str
    status: str          # bullish, neutral, defensive, or unavailable
    value: float | None
    detail: str
    as_of: str | None = None   # date of the last close used


@dataclass(frozen=True)
class Mood:
    as_of: str
    verdict: str
    checks: list[MoodCheck] = field(default_factory=list)

    @property
    def bullish(self) -> int:
        return sum(c.status == "bullish" for c in self.checks)

    @property
    def defensive(self) -> int:
        return sum(c.status == "defensive" for c in self.checks)

    @property
    def available(self) -> int:
        return sum(c.status != "unavailable" for c in self.checks)

    def to_json(self) -> dict:
        return {"as_of": self.as_of, "verdict": self.verdict, "bullish": self.bullish,
                "defensive": self.defensive, "available": self.available,
                "checks": [asdict(c) for c in self.checks]}

    @classmethod
    def from_json(cls, d: dict) -> "Mood":
        return cls(d["as_of"], d["verdict"], [MoodCheck(**c) for c in d.get("checks", [])])

    def text(self) -> str:
        lines = [f"Market mood as of {self.as_of}: {self.verdict.upper()} "
                 f"({self.bullish} bullish, {self.defensive} defensive of {self.available} checks available)"]
        lines += [f"- {c.label}: unavailable ({c.detail})" if c.status == "unavailable"
                  else f"- {c.label}: {c.status}. {c.detail}" for c in self.checks]
        if self.verdict == "unknown":
            lines.append("Too few series were available to call it. Unknown is not neutral: "
                         "report it as missing and do not guess the market's direction.")
        return "\n".join(lines)


MOOD_PROMPT = """Each event below also carries the market mood when the story broke: a count
of plain checks on the Nifty, FMCG, gold, the rupee and India VIX. It is about
the whole market, not this stock. Personas may weigh it (good news fades more
easily in a defensive market), but a stock call still has to rest on the
stock's own news. Where the mood says unknown or a check is unavailable, treat
it as missing, never as neutral."""


def _ret(close: pd.Series, n: int) -> float | None:
    return float(close.iloc[-1] / close.iloc[-1 - n] - 1) * 100 if len(close) > n else None


def _band(value: float, up: float, down: float, *, higher_is_defensive: bool = False) -> str:
    """bullish above `up`, defensive below `down` (or the reverse), else neutral."""
    if higher_is_defensive:
        return "defensive" if value >= up else "bullish" if value <= down else "neutral"
    return "bullish" if value >= up else "defensive" if value <= down else "neutral"


def _missing(id_: str, label: str, why: str) -> MoodCheck:
    return MoodCheck(id_, label, "unavailable", None, f"{why}; left out of the count")


def assess(series: dict[str, pd.DataFrame | None], as_of) -> Mood:
    """Score the checks from bars per series key (see SERIES); None for a missing series.

    Bars are cut to `as_of` here, so callers may pass the whole file.
    """
    as_of = pd.Timestamp(as_of)
    known: dict[str, pd.Series] = {}
    last: dict[str, str] = {}
    for key in SERIES:
        bars = series.get(key)
        if bars is None:
            continue
        cut = fx_bars_known_at(bars, as_of) if key in FX_SERIES else bars_known_at(bars, as_of)
        if not cut.empty:
            known[key] = cut["close"]
            last[key] = f"{cut.index[-1]:%Y-%m-%d}"

    def need(key: str, n: int) -> str | None:
        if key not in known:
            return f"no {SERIES[key]} prices"
        if len(known[key]) <= n:
            return f"only {len(known[key])} sessions of {SERIES[key]}, needs {n + 1}"
        return None

    checks: list[MoodCheck] = []

    label = f"Nifty {LOOKBACK}-session trend"
    if why := need("nifty", LOOKBACK):
        checks.append(_missing("nifty_trend", label, why))
    else:
        r = _ret(known["nifty"], LOOKBACK)
        checks.append(MoodCheck("nifty_trend", label, _band(r, 3, -3), round(r, 2),
                                f"Nifty {r:+.1f}% over {LOOKBACK} sessions (bullish at +3%, defensive at -3%).",
                                last["nifty"]))

    label = f"Nifty vs its {TREND_SMA}-day average"
    if why := need("nifty", TREND_SMA - 1):
        checks.append(_missing("nifty_long_trend", label, why))
    else:
        close = known["nifty"]
        gap = (float(close.iloc[-1]) / float(close.iloc[-TREND_SMA:].mean()) - 1) * 100
        checks.append(MoodCheck("nifty_long_trend", label, _band(gap, 2, -2), round(gap, 2),
                                f"Nifty is {gap:+.1f}% vs its {TREND_SMA}-day average "
                                "(bullish 2% above, defensive 2% below).", last["nifty"]))

    label = "Growth vs defensive (Nifty vs Nifty FMCG)"
    if why := need("nifty", LOOKBACK) or need("fmcg", LOOKBACK):
        checks.append(_missing("nifty_vs_fmcg", label, why))
    else:
        spread = _ret(known["nifty"], LOOKBACK) - _ret(known["fmcg"], LOOKBACK)
        checks.append(MoodCheck("nifty_vs_fmcg", label, _band(spread, 2, -2), round(spread, 2),
                                f"Nifty minus FMCG over {LOOKBACK} sessions {spread:+.1f} points; "
                                "FMCG leading means money is hiding in staples.",
                                min(last["nifty"], last["fmcg"])))

    label = "Safe havens (gold, dollar-rupee)"
    have = {k: _ret(known[k], LOOKBACK) for k in ("gold", "usdinr") if not need(k, LOOKBACK)}
    if not have:
        checks.append(_missing("safe_havens", label, "no GOLDBEES or USDINR prices"))
    else:
        # Gold up 3% in a month, or the rupee down 1.5%, is a flight to safety.
        # Both flat or falling means nobody is running for cover.
        gold, usd = have.get("gold"), have.get("usdinr")
        if (gold is not None and gold >= 3) or (usd is not None and usd >= 1.5):
            status = "defensive"
        elif all(v <= 0 for v in have.values()):
            status = "bullish"
        else:
            status = "neutral"
        parts = ([f"gold {gold:+.1f}%"] if gold is not None else ["gold n/a"]) + (
            [f"USD/INR {usd:+.1f}%"] if usd is not None else ["USD/INR n/a"])
        checks.append(MoodCheck("safe_havens", label, status, round(max(have.values()), 2),
                                f"{', '.join(parts)} over {LOOKBACK} sessions (defensive at gold +3% "
                                "or USD/INR +1.5%; bullish when both are flat or down).",
                                min(last[k] for k in have)))

    label = "India VIX"
    if "vix" not in known:
        checks.append(_missing("india_vix", label, "no INDIAVIX prices"))
    else:
        vix = float(known["vix"].iloc[-1])
        checks.append(MoodCheck("india_vix", label, _band(vix, 20, 14, higher_is_defensive=True), round(vix, 2),
                                f"India VIX {vix:.1f} (bullish at 14 or under, defensive at 20 or over).",
                                last["vix"]))

    mood = Mood(f"{as_of.tz_convert('Asia/Kolkata'):%Y-%m-%d %H:%M} IST", "unknown", checks)
    if mood.available < MIN_CHECKS:
        return mood
    if mood.bullish >= mood.defensive + 2:
        verdict = "bullish"
    elif mood.defensive >= mood.bullish + 2:
        verdict = "defensive"
    else:
        verdict = "neutral"
    return Mood(mood.as_of, verdict, checks)


def load_series(fetch, as_of) -> dict[str, pd.DataFrame | None]:
    """Bars for every series through `fetch(name, as_of) -> DataFrame`; None where it raises."""
    from .data.router import DataError

    out: dict[str, pd.DataFrame | None] = {}
    for key, name in SERIES.items():
        try:
            out[key] = fetch(name, as_of)
        except (DataError, FileNotFoundError, LookupError):
            out[key] = None
    return out


def from_router(router, as_of) -> Mood:
    """The mood through the data router (saved CSVs, then Yahoo, as configured)."""
    return assess(load_series(lambda name, ts: router.fetch("prices", name, ts)[1], as_of), as_of)
