from datetime import date

import pandas as pd
import pytest

from swingbot import realpos

CFG = {"start": "2027-10-31", "cap": 250_000, "review_drawdown": -0.25, "targets": {}}


def prices(folder, symbol, values, start="2027-11-01"):
    days = pd.bdate_range(start, periods=len(values))
    pd.DataFrame({"Date": days.strftime("%Y-%m-%d"), "Close": values}).to_csv(folder / f"{symbol}.csv", index=False)
    return days


def fresh():
    return {"lots": [], "sales": [], "notified": []}


def test_buys_the_written_plan_forbids_are_refused(tmp_path):
    prices(tmp_path, "etf", [100.0] * 5 + [70.0] * 5)
    s = fresh()
    with pytest.raises(ValueError, match="before 2027-10-31"):
        realpos.buy(s, CFG, tmp_path, date(2027, 10, 1), "etf", 10, 100)
    with pytest.raises(ValueError, match="cap"):
        realpos.buy(s, CFG, tmp_path, date(2027, 11, 1), "etf", 3000, 100)
    realpos.buy(s, CFG, tmp_path, date(2027, 11, 1), "etf", 100, 100)  # then it falls 30%
    with pytest.raises(ValueError, match="add nothing"):
        realpos.buy(s, CFG, tmp_path, date(2027, 11, 15), "etf", 10, 70)


def test_sales_take_the_oldest_units_and_get_their_own_term_and_tax_year():
    s = fresh()
    s["lots"] = [{"date": "2027-11-01", "symbol": "etf", "units": 10, "price": 100},
                 {"date": "2028-06-01", "symbol": "etf", "units": 10, "price": 120}]
    pieces = realpos.sell(s, date(2028, 12, 1), "etf", 15, 130)
    assert [(p["units"], p["gain"], p["long_term"]) for p in pieces] == [(10, 300, True), (5, 50, False)]
    assert {p["tax_year"] for p in pieces} == {"2028-29"}
    assert s["lots"] == [{"date": "2028-06-01", "symbol": "etf", "units": 5, "price": 120}]
    with pytest.raises(ValueError, match="only 5"):
        realpos.sell(s, date(2028, 12, 2), "etf", 6, 130)


def test_long_term_needs_more_than_twelve_months():
    assert not realpos.long_term(date(2027, 11, 1), date(2028, 11, 1))
    assert realpos.long_term(date(2027, 11, 1), date(2028, 11, 2))
    assert realpos.tax_year(date(2028, 3, 31)) == "2027-28" and realpos.tax_year(date(2028, 4, 1)) == "2028-29"


def test_harvest_room_is_the_exemption_left_after_this_years_booked_gains(tmp_path):
    prices(tmp_path, "etf", [300.0], start="2029-03-01")
    s = fresh()
    s["lots"] = [{"date": "2027-11-01", "symbol": "etf", "units": 1000, "price": 100}]  # Rs 2 lakh unrealised LT
    s["sales"] = [{"date": "2028-12-01", "symbol": "etf", "units": 1, "price": 1, "gain": 50_000,
                   "long_term": True, "tax_year": "2028-29"}]
    tv = realpos.tax_view(s, tmp_path, date(2029, 3, 2))
    assert tv["room"] == 75_000 and tv["harvestable"] == 75_000


def test_adding_money_is_not_a_gain_and_the_drawdown_alert_fires_once(tmp_path):
    prices(tmp_path, "etf", [100.0] * 3 + [100.0] * 3 + [70.0] * 2 + [100.0])
    s = fresh()
    realpos.buy(s, CFG, tmp_path, date(2027, 11, 1), "etf", 100, 100)
    realpos.buy(s, CFG, tmp_path, date(2027, 11, 4), "etf", 100, 100)  # doubling the money at a flat price
    said = []
    p = realpos.path(s, tmp_path)
    assert p["index"].iloc[3] == pytest.approx(1.0)  # no fake gain from the second buy
    s2 = {**s, "notified": []}
    path_at = p.iloc[:8]
    assert path_at["drawdown"].iloc[-1] == pytest.approx(-0.30)
    # Run the nightly check against the data up to the fall, then after the recovery.
    prices(tmp_path, "etf", [100.0] * 6 + [70.0] * 2)
    assert realpos.check(s2, CFG, tmp_path, date(2027, 11, 12), said.append)
    assert realpos.check(s2, CFG, tmp_path, date(2027, 11, 12), said.append) == []  # once only
    prices(tmp_path, "etf", [100.0] * 6 + [70.0] * 2 + [100.0])
    assert "recovered" in realpos.check(s2, CFG, tmp_path, date(2027, 11, 13), said.append)[0]
    assert len(said) == 2


def test_december_rebalance_and_march_harvest_reminders_come_once_a_year(tmp_path):
    prices(tmp_path, "fund", [100.0] * 3, start="2028-12-01")
    prices(tmp_path, "gold", [100.0] * 3, start="2028-12-01")
    cfg = {**CFG, "targets": {"fund": 0.7, "gold": 0.3}}
    s = fresh()
    s["lots"] = [{"date": "2028-12-01", "symbol": "fund", "units": 800, "price": 100},
                 {"date": "2028-12-01", "symbol": "gold", "units": 200, "price": 100}]
    said = []
    out = realpos.check(s, cfg, tmp_path, date(2028, 12, 5), said.append)
    assert len(out) == 1 and "fund 80% (target 70%)" in out[0]
    assert realpos.check(s, cfg, tmp_path, date(2028, 12, 6), said.append) == []
    prices(tmp_path, "fund", [150.0], start="2030-03-01")
    prices(tmp_path, "gold", [100.0], start="2030-03-01")
    s["lots"] = [{"date": "2028-12-01", "symbol": "fund", "units": 800, "price": 100}]
    out = realpos.check(s, cfg, tmp_path, date(2030, 3, 2), said.append)
    assert "Rs 40,000" in out[0]
    assert realpos.check(s, cfg, tmp_path, date(2030, 3, 3), said.append) == []


def test_report_before_any_buy_points_to_the_start_date():
    assert "2027-10-31" in realpos.report(fresh(), CFG, None, date(2026, 11, 14))
