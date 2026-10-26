"""Generate the SYNTHETIC sample in data/sample/.

Tickers, headlines and prices are all made up. They exist only so the pipeline
runs end to end with no network or API key. Do not read anything into the
numbers a backtest prints on this data.

    python scripts/make_sample_data.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[1] / "data" / "sample"

# (id, ticker, IST timestamp, headline, move over the next 3 sessions)
# The move is what the fake market "does"; some deliberately disagree with the
# headline tone so the sample has misses as well as hits.
EVENTS = [
    ("e01", "DEMOINFRA", "2026-01-06 08:30", "DemoInfra wins record highway contract worth Rs 4,000 crore", +0.05),
    ("e02", "DEMOBANK", "2026-01-09 16:10", "DemoBank Q3 profit misses estimates as bad loans rise", -0.04),
    ("e03", "DEMOPHARMA", "2026-01-14 11:45", "DemoPharma gets US FDA approval for generic drug", +0.03),
    ("e04", "DEMOINFRA", "2026-01-21 18:00", "DemoInfra promoter pledge rises, brokerage issues warning", -0.03),
    ("e05", "DEMOTECH", "2026-01-28 07:50", "DemoTech beats revenue estimates and raises guidance", -0.02),
    ("e06", "DEMOBANK", "2026-02-03 09:00", "DemoBank announces buyback at 20% premium", +0.04),
    ("e07", "DEMOPHARMA", "2026-02-10 15:40", "SEBI probe into DemoPharma related-party deals", -0.06),
    ("e08", "DEMOTECH", "2026-02-17 12:20", "DemoTech CFO resigns with immediate effect", -0.03),
    ("e09", "DEMOINFRA", "2026-02-24 08:05", "DemoInfra order book growth strong, analysts upgrade to buy", +0.02),
    ("e10", "DEMOBANK", "2026-03-03 17:30", "DemoBank board meeting scheduled to consider fund raising", +0.01),
    ("e11", "DEMOPHARMA", "2026-03-10 10:15", "DemoPharma plant gets clean report, no observations", +0.02),
    ("e12", "DEMOTECH", "2026-03-17 19:00", "DemoTech downgraded to underperform on weak deal pipeline", +0.01),
]

TICKERS = sorted({e[1] for e in EVENTS})


def trading_days(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)  # weekdays only; holidays ignored for the sample


def make_prices(ticker: str, days: pd.DatetimeIndex, rng: np.random.Generator) -> pd.DataFrame:
    n = len(days)
    drift = rng.normal(0, 0.012, n)
    for _, t, ts, _, move in EVENTS:
        if t != ticker:
            continue
        stamp = pd.Timestamp(ts)
        first = days.searchsorted(stamp.normalize())
        if stamp.time() >= pd.Timestamp("09:15").time():
            first += 1
        for k in range(3):
            if first + k < n:
                drift[first + k] += move / 3
    close = 1000 * np.exp(np.cumsum(drift))
    open_ = close / np.exp(drift) * np.exp(rng.normal(0, 0.002, n))
    # Put the news-day jump into the session, not the gap, so entry-at-open captures it.
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    vol = rng.integers(200_000, 2_000_000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=pd.Index(days.date, name="date"),
    ).round(2)


def main() -> None:
    rng = np.random.default_rng(7)
    days = trading_days("2025-12-01", "2026-04-30")
    (OUT / "prices").mkdir(parents=True, exist_ok=True)
    for t in TICKERS:
        make_prices(t, days, rng).to_csv(OUT / "prices" / f"{t}.csv")
    bench = make_prices("NIFTY50", days, rng)
    bench[["open", "high", "low", "close"]] *= 22.0
    bench.round(2).to_csv(OUT / "prices" / "NIFTY50.csv")

    pd.DataFrame(
        [
            {"event_id": i, "ticker": t, "published_at": f"{ts}+05:30", "headline": h, "body": "", "source": "synthetic"}
            for i, t, ts, h, _ in EVENTS
        ]
    ).to_csv(OUT / "events.csv", index=False)
    print(f"wrote {len(EVENTS)} events and {len(TICKERS) + 1} price files to {OUT}")


if __name__ == "__main__":
    main()
