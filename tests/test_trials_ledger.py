"""Every pre-registered study must be listed in docs/research/trials-ledger.md,
so the count of things tried can't quietly shrink."""

from pathlib import Path

from swingbot.research import ledger_missing

RESEARCH = Path(__file__).resolve().parent.parent / "docs" / "research"


def test_every_prereg_is_in_the_trials_ledger():
    missing = ledger_missing(RESEARCH)
    assert not missing, f"add these studies to docs/research/trials-ledger.md: {missing}"
