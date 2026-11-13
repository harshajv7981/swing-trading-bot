"""The two TradingAgents forward tests are judged on rules fixed before the first
call. Their skills and pre-registrations must not drift while calls are being
logged, so any edit to them fails here. (Idea from LangAlpha's docstring lock.)

If an edit is deliberate (a fix both tests can live with), say what changed and
why in the prereg's own change notes, then update the hash below in the same
commit. Unlock after both tests are judged (the large-cap one after 31 May 2027).
"""

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LOCKED = {
    ".claude/skills/trading-agents/SKILL.md": "8b930c25fc266c45c204c9664af48f09d535c9fab53ca75c47ea51814e85ed9f",
    ".claude/skills/tradingagents-weekly/SKILL.md": "d40a215778cb22ea9971031afe627b92766338cb3bedbd7e1f9fe8d13d50a5e6",
    "docs/research/trading-agents-prereg.md": "ad917cc431a62f2c22ef16859ea351e377bc2e197d49505a9de95a33cd3fb0cc",
    "docs/research/tradingagents-paper-test.md": "0291bea70aa4ed4e77bc0a5f6ba9387fb93b55abbb9f41dbc45a2b6a510b9509",
}


@pytest.mark.parametrize("rel", sorted(LOCKED))
def test_agent_test_files_are_unchanged_while_the_tests_run(rel):
    got = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
    assert got == LOCKED[rel], (
        f"{rel} changed while the TradingAgents forward test is running. If deliberate, "
        "note the change in the prereg and update its hash in tests/test_agent_test_lock.py."
    )
