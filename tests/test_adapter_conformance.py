"""One contract, every execution adapter: the same tests run against the in-memory
reference adapter and the Alpaca paper adapter (on a fake Alpaca)."""

from datetime import UTC, datetime

import pytest
from alpaca_fake import FakeAlpaca

from swingbot.broker.alpaca import AlpacaAPI, AlpacaBroker, AlpacaConfig
from swingbot.markets import Fill, OrderIntent, OrderStatus, OrderUpdate
from swingbot.markets.adapter import AdapterRefused, SimulatedAdapter

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


def buy(cid="us-r1-0001", qty=10, **kw):
    return OrderIntent(market="us", client_order_id=cid, symbol="AAPL", side="buy", qty=qty, order_type="limit",
                       limit_price=190.25, time_in_force="day", created_utc=T0, **kw)


@pytest.fixture(params=["simulated", "alpaca"])
def pair(request, tmp_path):
    """(adapter, venue): the venue's execute/reject/expire stand in for the market."""
    if request.param == "simulated":
        sim = SimulatedAdapter("us", state_root=tmp_path)
        return sim, sim
    fake = FakeAlpaca()
    cfg = AlpacaConfig(enabled=True, state_root=tmp_path)
    return AlpacaBroker(AlpacaAPI(cfg, "kid", "sec", transport=fake), cfg.state_dir), fake


def fills(events):
    return [e for e in events if isinstance(e, Fill)]


def statuses(events):
    return [e.status for e in events if isinstance(e, OrderUpdate)]


def test_a_submitted_order_is_accepted_and_listed_open(pair):
    adapter, _ = pair
    up = adapter.submit(buy())
    assert up.status is OrderStatus.ACCEPTED and up.client_order_id == "us-r1-0001" and up.broker_order_id
    assert [o.client_order_id for o in adapter.open_orders()] == ["us-r1-0001"]


def test_partial_then_full_fill_each_fill_reported_once(pair):
    adapter, venue = pair
    adapter.submit(buy())
    venue.execute("us-r1-0001", 4, 190.2)
    ev = adapter.sync()
    assert [f.qty for f in fills(ev)] == [4] and statuses(ev) == [OrderStatus.PARTIALLY_FILLED]
    venue.execute("us-r1-0001", 6, 190.25)
    ev = adapter.sync()
    assert [f.qty for f in fills(ev)] == [6] and statuses(ev) == [OrderStatus.FILLED]
    assert adapter.sync() == []
    assert adapter.open_orders() == []
    [pos] = adapter.positions()
    assert (pos.symbol, pos.qty, pos.currency) == ("AAPL", 10, "USD")
    assert pos.avg_price == pytest.approx((4 * 190.2 + 6 * 190.25) / 10)


def test_submitting_the_same_client_order_id_twice_places_one_order(pair):
    adapter, venue = pair
    a, b = adapter.submit(buy()), adapter.submit(buy())
    assert a.broker_order_id == b.broker_order_id
    venue.execute("us-r1-0001", 10, 190.0)
    assert sum(f.qty for f in fills(adapter.sync())) == 10


def test_cancel_an_open_order_but_not_a_filled_one(pair):
    adapter, venue = pair
    adapter.submit(buy())
    assert adapter.cancel("us-r1-0001").status is OrderStatus.CANCELLED
    assert adapter.open_orders() == []
    adapter.submit(buy("us-r1-0002"))
    venue.execute("us-r1-0002", 10, 190.0)
    adapter.sync()
    with pytest.raises(AdapterRefused):
        adapter.cancel("us-r1-0002")
    with pytest.raises(AdapterRefused):
        adapter.cancel("no-such-order")


def test_rejections_and_expiries_are_final(pair):
    adapter, venue = pair
    adapter.submit(buy("us-r1-0001"))
    adapter.submit(buy("us-r1-0002"))
    venue.reject("us-r1-0001")
    venue.expire("us-r1-0002")
    assert sorted(statuses(adapter.sync())) == sorted([OrderStatus.REJECTED, OrderStatus.EXPIRED])
    assert adapter.open_orders() == [] and adapter.positions() == []


def test_an_order_for_another_market_is_refused(pair):
    adapter, _ = pair
    nse = OrderIntent(market="nse", client_order_id="n1", symbol="TCS", side="buy", qty=1, order_type="limit",
                      limit_price=3001.1, time_in_force="day", created_utc=T0)
    with pytest.raises(AdapterRefused, match="nse"):
        adapter.submit(nse)
