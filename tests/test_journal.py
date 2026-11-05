import json

import pytest

from swingbot.broker import journal


def order(oid, side, qty, ref, limit, filled, avg, status="traded", month="2026-10-30", sym=None):
    return {"id": oid, "dhan_id": "1", "month": month, "symbol": sym or f"S{oid}", "side": side, "qty": qty,
            "ref_price": ref, "limit": limit, "sent_at": "2026-11-02 10:00", "status": status,
            "filled": filled, "avg_price": avg}


LEDGER = {"months": [{"date": "2026-10-30", "holdings": {"S1": {"price": 99.0}},
                      "orders": [{"symbol": "S2", "price": 200.0}]}]}


def test_slippage_is_signed_as_a_cost_on_both_sides():
    book = {"orders": [order(1, "BUY", 10, 100.0, 100.5, 10, 100.2),   # paid 20 bps over the quote
                       order(2, "SELL", 5, 202.0, 201.0, 5, 201.5)]}   # got less than the quote
    r1, r2 = journal.rows(book, LEDGER)
    assert r1["vs_quote_bps"] == pytest.approx(20) and r1["vs_close_bps"] == pytest.approx((100.2 / 99 - 1) * 1e4)
    assert r2["vs_quote_bps"] == pytest.approx((1 - 201.5 / 202) * 1e4)
    assert r2["vs_close_bps"] == pytest.approx(-75)   # sold above the close: a gain, so negative


def test_months_weight_by_value_and_count_unfilled_and_suspect():
    book = {"orders": [order(1, "BUY", 10, 100.0, 100.5, 10, 101.0),
                       order(2, "BUY", 30, 100.0, 100.5, 10, 100.0),          # part fill, no slippage
                       order(3, "BUY", 5, 100.0, 100.5, 0, None, status="rejected"),
                       order(4, "BUY", 5, 100.0, 100.5, 5, 50.0)]}            # sandbox placeholder fill
    (m,) = journal.months(journal.rows(book))
    assert (m["orders"], m["full"], m["part"], m["none"], m["suspect"]) == (4, 2, 1, 1, 1)
    assert m["vs_quote_bps"] == pytest.approx(100 * 1010 / (1010 + 1000))
    assert m["fill_rate"] == pytest.approx(25 / 50)
    assert set(m["unfilled"]) == {"buy S2 (traded)", "buy S3 (rejected)"}


def test_render_and_write(tmp_path):
    assert "No momentum orders sent yet" in journal.render([], live=False)
    state = tmp_path / "state"
    state.mkdir()
    (state / journal.BOOK).write_text(json.dumps({"orders": [order(1, "BUY", 10, 100.0, 100.5, 10, 100.2)]}))
    (tmp_path / "ledger.json").write_text(json.dumps(LEDGER))
    out, ms = journal.write(state, tmp_path / "ledger.json", live=False, out=tmp_path / "j.md")
    text = out.read_text()
    assert "Dhan sandbox" in text and "| S1 | buy | 10 | 10 | traded |" in text and ms[0]["full"] == 1
    assert "+20 bps against the planning quote" in text
