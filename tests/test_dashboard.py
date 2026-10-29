from html.parser import HTMLParser

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


