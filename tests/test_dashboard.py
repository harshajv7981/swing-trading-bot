from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from test_walkforward import data, views  # noqa: F401  (the fixture)

from swingbot.dashboard import Sources, gather, main, render
from swingbot.risk import KillSwitch
from swingbot.walkforward import Run


class Balanced(HTMLParser):
    VOID = frozenset({"meta", "br", "img", "input", "link", "hr"})

    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, (tag, self.stack[-3:])
        self.stack.pop()


def balanced(page: str) -> bool:
    p = Balanced()
    p.feed(page)
    return p.stack == []


def test_dashboard_of_a_walk_forward_run(data, tmp_path):  # noqa: F811
    tmp, prices, events, holidays = data
    run = Run(tmp / "wf")
    run.init(date(2026, 11, 2), date(2026, 11, 6), 1_000_000, prices, events, str(holidays))
    run.next(lambda _: None)
    views(tmp / "wf/days/2026-11-03/views.jsonl", "e1", 0.7)
    run.next(lambda _: None)
    # Entered 3 Nov on a 72h call: held to the 5 Nov close (the time exit), so look on the 4th.
    d = gather(Sources.walkforward(tmp / "wf"), "2026-11-04 18:00")
    assert d["has_account"] and [p["ticker"] for p in d["positions"]] == ["TCS"]
    assert d["positions"][0]["stop"] and d["open_risk"] > 0
    assert len(d["curve"]) >= 2 and "bench" in d["curve"][-1]
    page = render(d, "test")
    assert "TCS" in page and "Kill switch LATCHED" not in page and "<svg" in page
    assert balanced(page)
    # Read-only: the kill switch, the ledger and the book are untouched.
    before = {p: p.read_bytes() for p in (tmp / "wf").rglob("*") if p.is_file()}
    gather(Sources.walkforward(tmp / "wf"), "2026-11-04 18:00")
    assert before == {p: p.read_bytes() for p in (tmp / "wf").rglob("*") if p.is_file()}

    KillSwitch(tmp / "wf/state").latch("test <b>reason</b>")
    page = render(gather(Sources.walkforward(tmp / "wf"), "2026-11-04 18:00"))
    assert "Kill switch LATCHED: test &lt;b&gt;reason&lt;/b&gt;" in page


def test_dashboard_with_no_account(tmp_path, capsys):
    out = tmp_path / "d.html"
    assert main(["--ledger", str(tmp_path / "none.jsonl"), "--state-dir", str(tmp_path), "--runs-dir",
                 str(tmp_path / "runs"), "--out", str(out)]) == 0
    page = out.read_text()
    assert "No paper account yet" in page and balanced(page)


def test_dashboard_of_a_walk_forward_run_uses_its_risk_limits(data):  # noqa: F811
    tmp, prices, events, holidays = data
    limits = tmp / "risk.toml"
    limits.write_text(Path("config/risk.toml").read_text().replace("weekly_kill_pct = 5.0", "weekly_kill_pct = 3.0"))
    Run(tmp / "wf").init(date(2026, 11, 2), date(2026, 11, 6), 1_000_000, prices, events, str(holidays),
                         str(limits))
    assert gather(Sources.walkforward(tmp / "wf"), "2026-11-02 18:00")["kill_pct"] == 3.0
    assert gather(Sources.walkforward(tmp / "wf", "config/risk.toml"), "2026-11-02 18:00")["kill_pct"] == 5.0
