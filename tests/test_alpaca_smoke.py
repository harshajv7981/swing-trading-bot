"""Phase 2: the smoke test that checks the Alpaca adapter against a real paper account.
Here it runs against the fake, to prove the checks pass when Alpaca behaves as documented
and fail when it does not."""

from datetime import UTC, datetime

from alpaca_fake import FakeAlpaca

from swingbot.broker.alpaca import AlpacaAPI, AlpacaConfig
from swingbot.broker.alpaca_smoke import run_smoke

OPEN = datetime(2026, 11, 9, 15, 30, tzinfo=UTC)      # Monday 10:30 New York
CLOSED = datetime(2026, 11, 7, 15, 30, tzinfo=UTC)    # Saturday


def api(tmp_path, fake):
    return AlpacaAPI(AlpacaConfig(enabled=True, state_root=tmp_path), "kid", "sec", transport=fake)


def no_sleep(_):
    pass


def test_quiet_phase_passes_when_alpaca_behaves_as_documented(tmp_path):
    fake = FakeAlpaca()
    res = run_smoke(api(tmp_path, fake), "quiet", CLOSED, tmp_path / "smoke", sleep=no_sleep)
    assert res["ok"], res["checks"]
    names = [c["check"] for c in res["checks"]]
    assert names == ["account", "accepted", "duplicate refused", "found by client id", "cancelled",
                     "sync reports no fills twice"]
    assert [o["status"] for o in fake.orders.values()] == ["canceled"]
    assert not any(c[1] == "/v2/positions" for c in fake.calls if c[0] != "GET")
    assert (tmp_path / "smoke" / "quiet.md").exists()


def test_quiet_phase_fails_if_alpaca_takes_a_reused_client_order_id(tmp_path):
    fake = FakeAlpaca()
    fake.allow_duplicates = True
    res = run_smoke(api(tmp_path, fake), "quiet", CLOSED, tmp_path / "smoke", sleep=no_sleep)
    assert not res["ok"]
    assert [c for c in res["checks"] if not c["ok"]][0]["check"] == "duplicate refused"


def test_fill_phase_buys_and_sells_one_share_and_reconciles(tmp_path):
    fake = FakeAlpaca()
    fake.auto_fill = 500.0
    fake.positions["AAPL"] = {"symbol": "AAPL", "qty": "3", "avg_entry_price": "200"}   # the trial's, untouched
    res = run_smoke(api(tmp_path, fake), "fill", OPEN, tmp_path / "smoke", sleep=no_sleep)
    assert res["ok"], res["checks"]
    assert [c["check"] for c in res["checks"]] == ["account", "buy filled", "one fill recorded", "position up one",
                                                   "sync does not repeat the fill", "sell filled",
                                                   "position back to start"]
    assert "SPY" not in fake.positions and fake.positions["AAPL"]["qty"] == "3"
    assert (tmp_path / "smoke" / "fill-passed.json").exists()
    again = run_smoke(api(tmp_path, fake), "fill", OPEN, tmp_path / "smoke", sleep=no_sleep)
    assert again["skipped"] == "already passed" and len(fake.orders) == 2


def test_fill_phase_waits_for_market_hours(tmp_path):
    fake = FakeAlpaca()
    res = run_smoke(api(tmp_path, fake), "fill", CLOSED, tmp_path / "smoke", sleep=no_sleep)
    assert res["skipped"] == "market closed" and fake.orders == {}


def test_a_buy_that_never_fills_fails_the_fill_phase_without_selling(tmp_path):
    fake = FakeAlpaca()                                   # no auto fill: the order just sits there
    res = run_smoke(api(tmp_path, fake), "fill", OPEN, tmp_path / "smoke", sleep=no_sleep)
    assert not res["ok"] and res["checks"][-1]["check"] == "buy filled"
    assert all(o["side"] == "buy" for o in fake.orders.values())
    assert not (tmp_path / "smoke" / "fill-passed.json").exists()
