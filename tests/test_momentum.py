import json

import pandas as pd
import pytest

from swingbot import momentum
from swingbot.risk.calendar import NSECalendar

N = 10
KNOWN_2025 = NSECalendar({2025: set()})  # every weekday a session


@pytest.fixture(autouse=True)
def small_rule(monkeypatch):
    # Ten stocks: ranks 3-6 by traded value are "mid", and the best 2 of those are held.
    monkeypatch.setattr(momentum, "RANKS", (2, 6))
    monkeypatch.setattr(momentum, "TOP", 2)
    monkeypatch.setattr(momentum, "MIN_HISTORY", 30)


def write_stocks(folder, end="2025-08-29", growth=None, extra=None):
    """S0 trades the most value, S9 the least; S9 grows fastest, S0 slowest."""
    folder.mkdir(exist_ok=True)
    days = pd.bdate_range("2025-01-01", end)
    for k in range(N):
        g = (growth or {}).get(k, 0.001 * k)
        close = pd.Series([100 * (1 + g) ** t for t in range(len(days))], index=days)
        df = pd.DataFrame({"Date": days.strftime("%Y-%m-%d"), "Close": close.round(4).values,
                           "Volume": ((N - k) * 1e8 / close).astype(int).values, "Series": "EQ"})
        if extra and k in extra:
            df = extra[k](df)
        df.to_csv(folder / f"s{k}.csv", index=False)
    (folder / "nifty midcap 150.csv").write_text("Date,Close\n2025-07-31,100\n2025-08-29,110\n")


def test_select_takes_the_best_momentum_inside_the_mid_ranks(tmp_path):
    write_stocks(tmp_path / "d")
    px, val = momentum.load(tmp_path / "d")
    bucket, picks = momentum.select(px, val, pd.Timestamp("2025-07-31"))
    assert bucket == ["S2", "S3", "S4", "S5"]  # S6-S9 grow faster but are outside the ranks
    assert picks == ["S5", "S4"]


def test_select_refuses_a_day_that_is_not_a_month_end(tmp_path):
    write_stocks(tmp_path / "d")
    px, val = momentum.load(tmp_path / "d")
    with pytest.raises(ValueError, match="not the last session"):
        momentum.select(px, val, pd.Timestamp("2025-07-30"))


def test_update_books_each_finished_month_once(tmp_path):
    write_stocks(tmp_path / "d")
    ledger = tmp_path / "ledger.json"
    done = momentum.update(tmp_path / "d", ledger, KNOWN_2025, start="2025-07-01")
    assert [m["date"] for m in done] == ["2025-07-31", "2025-08-29"]
    first = done[0]
    assert set(first["holdings"]) == {"S4", "S5"}
    assert {o["side"] for o in first["orders"]} == {"buy"}
    assert first["costs"] == pytest.approx(sum(o["shares"] * o["price"] for o in first["orders"]) * 0.002)
    assert first["cash"] >= 0
    assert first["cash"] + sum(h["shares"] * h["price"] for h in first["holdings"].values()) \
        == pytest.approx(1_000_000 - first["costs"])
    assert first["indexes"] == {"Nifty Midcap 150": 100.0}
    assert momentum.update(tmp_path / "d", ledger, KNOWN_2025, start="2025-07-01") == []
    assert len(json.loads(ledger.read_text())["months"]) == 2


def test_a_month_is_not_booked_until_the_data_finishes_it(tmp_path):
    write_stocks(tmp_path / "d")
    # Without a holiday list for 2025, 29 Aug cannot be known as August's last session.
    done = momentum.update(tmp_path / "d", tmp_path / "ledger.json", NSECalendar(), start="2025-07-01")
    assert [m["date"] for m in done] == ["2025-07-31"]
    write_stocks(tmp_path / "d", end="2025-08-27")
    assert momentum.update(tmp_path / "d", tmp_path / "l2.json", KNOWN_2025, start="2025-08-01") == []


def test_holdings_are_marked_to_the_next_month_end(tmp_path):
    write_stocks(tmp_path / "d")
    jul, aug = momentum.update(tmp_path / "d", tmp_path / "ledger.json", KNOWN_2025, start="2025-07-01")
    px = {k: pd.read_csv(tmp_path / "d" / f"s{k}.csv", index_col="Date")["Close"] for k in (4, 5)}
    held = sum(jul["holdings"][f"S{k}"]["shares"] * px[k]["2025-08-29"] for k in (4, 5))
    assert aug["value"] == pytest.approx(jul["cash"] + held)
    assert aug["ew"]["level"] < 1.1 and aug["ew"]["names"] == ["S2", "S3", "S4", "S5"]


def test_a_split_after_booking_scales_the_shares(tmp_path):
    write_stocks(tmp_path / "d")
    _, aug = momentum.update(tmp_path / "d", tmp_path / "ledger.json", KNOWN_2025, start="2025-07-01")

    def split(df):  # 2-for-1 on 11 Aug, left unadjusted in the file as eod2_data sometimes does
        df.loc[df["Date"] >= "2025-08-11", "Close"] /= 2
        return df

    write_stocks(tmp_path / "d2", end="2025-08-08")  # July booked before the split
    momentum.update(tmp_path / "d2", tmp_path / "l2.json", KNOWN_2025, start="2025-07-01")
    write_stocks(tmp_path / "d2", extra={5: split})
    (aug2,) = momentum.update(tmp_path / "d2", tmp_path / "l2.json", KNOWN_2025, start="2025-07-01")
    assert aug2["value"] == pytest.approx(aug["value"], rel=1e-6)
    assert any(n.startswith("S5: back-adjusted close") for n in aug2["notes"])


def test_a_holding_moved_to_the_be_series_is_still_priced_and_sold(tmp_path):
    def to_be(df):
        df.loc[df["Date"] >= "2025-08-01", "Series"] = "BE"
        return df

    write_stocks(tmp_path / "d", extra={5: to_be})
    jul, aug = momentum.update(tmp_path / "d", tmp_path / "ledger.json", KNOWN_2025, start="2025-07-01")
    assert "S5" in jul["holdings"] and "S5" not in aug["holdings"]  # out of the ranking, sold in BE
    sale = next(o for o in aug["orders"] if o["symbol"] == "S5")
    assert sale["side"] == "sell"
    assert sale["price"] == pytest.approx(pd.read_csv(tmp_path / "d" / "s5.csv", index_col="Date")["Close"]["2025-08-29"])


def test_report_shows_months_holdings_and_orders(tmp_path):
    write_stocks(tmp_path / "d")
    ledger = tmp_path / "ledger.json"
    assert "No month booked yet" in momentum.report(momentum.new_ledger())
    momentum.update(tmp_path / "d", ledger, KNOWN_2025, start="2025-07-01")
    text = momentum.report(json.loads(ledger.read_text()))
    assert "| 2025-08-29 |" in text and "Holdings from 2025-08-29" in text and "| S5 |" in text


def test_gold_shadow_holds_30_percent_gold_and_rebalances_in_december():
    def month(date, value, gold):
        return {"date": date, "value": value, "indexes": {momentum.GOLD: gold}}

    ledger = {"capital": 100.0, "months": [month("2025-10-31", 100.0, 10.0), month("2025-11-28", 110.0, 10.0),
                                           month("2025-12-31", 110.0, 20.0), month("2026-01-30", 110.0, 20.0)]}
    out = momentum.gold_mix(ledger)
    gold0 = 0.3 * (1 - momentum.GOLD_COST)
    assert out[0] == pytest.approx(0.7 + gold0)
    assert out[1] == pytest.approx(0.77 + gold0)  # stocks up 10%, gold flat
    total = 0.77 + 2 * gold0  # gold doubled, then back to 70/30 at the December booking
    moved = 2 * gold0 - 0.3 * total
    assert out[2] == pytest.approx(total - moved * (momentum.COST_SIDE + momentum.GOLD_COST))
    assert out[3] == pytest.approx(out[2])
    ledger["months"][1]["indexes"] = {}
    assert momentum.gold_mix(ledger)[1:] == [None, None, None]
