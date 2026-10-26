
import numpy as np
import pandas as pd
import pytest

from swingbot.cli import briefs_main
from swingbot.data import DataConfig, NoData, Router, VendorUnavailable, parse_as_of
from swingbot.data import vendors
from swingbot.data.cli import main as data_main
from swingbot.models import IST
from swingbot.swarm import RecordedSwarm

from test_backtest import bars


def walk(n=60, start="2026-01-01"):
    rng = np.random.default_rng(3)
    return bars(list(100 * np.cumprod(1 + rng.normal(0, 0.01, n))), start=start)


@pytest.fixture
def setup(tmp_path):
    prices = tmp_path / "prices"
    prices.mkdir()
    walk().to_csv(prices / "UP.csv", index_label="date")  # 1 Jan .. 25 Mar 2026
    events = tmp_path / "events.csv"
    events.write_text(
        "event_id,ticker,published_at,headline,body,source\n"
        "n1,UP,2026-03-02 09:00,Old story,,gdelt:x\n"
        "n2,UP,2026-03-05 10:00,Contract win,,gdelt:x\n"
        "f1,UP,2026-03-04 17:00,Financial Results for the quarter,Profit up 12%,nse-filing\n"
        "n3,UP,2026-03-06 12:00,Later story,,gdelt:x\n"
        "x1,OTHER,2026-03-05 10:00,Someone else,,gdelt:x\n"
    )
    cfg = DataConfig(sources={"prices": "csv", "news": "events", "filings": "events",
                              "fundamentals": "nse_results,yfinance"},
                     prices_dir=prices, events_file=events, cache_prices=False)
    return Router(cfg), tmp_path


def test_parse_as_of():
    assert parse_as_of("2026-03-05") == pd.Timestamp("2026-03-05 23:59:59", tz=IST)
    assert parse_as_of("2026-03-05 11:00") == pd.Timestamp("2026-03-05 11:00", tz=IST)


def test_nothing_after_as_of_is_served(setup):
    router, _ = setup
    at = parse_as_of("2026-03-05 11:00")
    news = router.text("news", "UP", at)
    assert "Contract win" in news and "Old story" in news
    assert "Later story" not in news and "Someone else" not in news
    prices = router.text("prices", "UP", at, rows=5)
    assert "2026-03-04" in prices and "2026-03-05" not in prices  # 11:00 is before the close
    assert "Technicals as of the 2026-03-04 close" in router.text("technicals", "UP", at)


def test_point_in_time_fundamentals_and_withheld_live_data(setup):
    router, _ = setup
    assert "Profit up 12%" in router.text("fundamentals", "UP", parse_as_of("2026-03-05"))
    early = router.text("fundamentals", "UP", parse_as_of("2026-03-03"))  # before the filing, and in the past
    assert early.startswith("WITHHELD") and "present-day" in early


def test_chain_falls_through_and_unavailable_outranks_no_data(setup, monkeypatch):
    router, _ = setup
    def down(*_):
        raise VendorUnavailable("timeout")
    def served(*_):
        return walk()
    monkeypatch.setitem(vendors.REGISTRY["prices"], "down", down)
    monkeypatch.setitem(vendors.REGISTRY["prices"], "served", served)
    at = parse_as_of("2026-03-05")

    router.config = router.config.with_sources(prices="csv,served")
    assert router.fetch("prices", "MISSING", at)[0] == "served"  # csv has no file, next vendor serves

    router.config = router.config.with_sources(prices="csv,down")
    with pytest.raises(VendorUnavailable):
        router.fetch("prices", "MISSING", at)
    assert router.text("prices", "MISSING", at).startswith("DATA_UNAVAILABLE")

    router.config = router.config.with_sources(prices="csv")
    assert router.text("prices", "MISSING", at).startswith("NO_DATA")
    router.config = router.config.with_sources(prices="nonsense")
    with pytest.raises(ValueError, match="available"):
        router.text("prices", "UP", at)


def test_stale_csv_is_no_data(setup):
    router, _ = setup
    with pytest.raises(NoData, match="stale"):
        router.fetch("prices", "UP", parse_as_of("2026-06-01"))


def test_yahoo_downloads_merge_into_saved_csv(tmp_path, monkeypatch):
    path = tmp_path / "UP.csv"
    old = walk(10)
    old.to_csv(path, index_label="date")
    new = walk(20, start="2026-01-08") * 1.01
    vendors._merge_into_csv(path, new)
    merged = pd.read_csv(path, index_col="date", parse_dates=True)
    assert len(merged) == len(old.index.union(new.index))
    assert merged["close"].iloc[-1] == pytest.approx(new["close"].iloc[-1])


def test_cli_prints_every_section(setup, capsys, tmp_path):
    router, root = setup
    cfg = root / "sources.toml"
    cfg.write_text(f'[paths]\nprices_dir = "{root / "prices"}"\nevents_file = "{root / "events.csv"}"\n'
                   '[sources]\nprices = "csv"\n')
    data_main(["all", "up", "--as-of", "2026-03-05", "--config", str(cfg)])
    out = capsys.readouterr().out
    for marker in ("# Daily bars for UP", "Technicals as of", "# News for UP", "# Exchange filings", "Results filings"):
        assert marker in out


def test_briefs_context_excludes_the_event_and_later_news(setup, tmp_path):
    _, root = setup
    out = tmp_path / "brief.md"
    briefs_main(["--events", str(root / "events.csv"), "--cutoff", "none", "--with-context",
                 "--with-technicals", str(root / "prices"), "--out", str(out)])
    text = out.read_text()
    n2 = text.split("### n2")[1].split("###")[0]
    assert "Old story" in n2 and "Profit up 12%" in n2
    assert n2.count("Contract win") == 1  # its own headline, not repeated as context
    assert "Later story" not in n2
    assert "## Analyst reports" in text


def test_analyst_lines_are_kept_but_not_scored(tmp_path):
    f = tmp_path / "v.jsonl"
    personas = ["Retail Bull", "Institutional Bear", "Foreign Institutional Investor", "Financial Media"]
    import json
    rows = [{"event_id": "e1", "role": "analyst", "analyst": "market", "report": "Downtrend."}]
    rows += [{"event_id": "e1", "persona": p, "stance": 0.5, "conviction": 0.5} for p in personas]
    f.write_text("\n".join(json.dumps(r) for r in rows))
    swarm = RecordedSwarm(f)
    assert swarm.reports["e1"]["market"] == "Downtrend."
    f.write_text(json.dumps({"event_id": "e1", "role": "analyst", "analyst": "astrology", "report": "x"}))
    with pytest.raises(ValueError, match="unknown analyst"):
        RecordedSwarm(f)
