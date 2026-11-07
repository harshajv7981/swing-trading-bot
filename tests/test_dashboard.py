import json
from html.parser import HTMLParser
from pathlib import Path

from swingbot.dashboard import main


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


def test_dashboard_with_no_account(tmp_path, capsys):
    out = tmp_path / "d.html"
    assert main(["--ledger", str(tmp_path / "none.jsonl"), "--state-dir", str(tmp_path), "--runs-dir",
                 str(tmp_path / "runs"), "--out", str(out)]) == 0
    page = out.read_text()
    assert "No paper account yet" in page and balanced(page)




def test_the_open_risk_tile_shows_the_limit_from_the_risk_config(tmp_path):
    risk = tmp_path / "risk.toml"
    risk.write_text((Path(__file__).parent.parent / "config" / "risk.toml").read_text()
                    .replace("max_open_risk_pct = 5.0", "max_open_risk_pct = 6.5"))
    (tmp_path / "data.toml").write_text(f'[paths]\nprices_dir = "{tmp_path / "prices"}"\n')
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"kind": "open", "at": "2026-11-02 09:00", "cash": 1000000}) + "\n")
    out = tmp_path / "d.html"
    assert main(["--ledger", str(ledger), "--state-dir", str(tmp_path), "--runs-dir", str(tmp_path / "runs"),
                 "--data-config", str(tmp_path / "data.toml"), "--config", str(risk), "--out", str(out)]) == 0
    assert "(limit 6.5%)" in out.read_text()
