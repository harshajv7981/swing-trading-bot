"""Every pre-registered study must be listed in docs/research/trials-ledger.md,
so the count of things tried can't quietly shrink."""

from pathlib import Path

RESEARCH = Path(__file__).resolve().parent.parent / "docs" / "research"


def test_every_prereg_is_in_the_trials_ledger():
    ledger = (RESEARCH / "trials-ledger.md").read_text()
    missing = [p.name for p in sorted(RESEARCH.glob("*-prereg.md")) if f"]({p.name})" not in ledger]
    assert not missing, f"add these studies to docs/research/trials-ledger.md: {missing}"
