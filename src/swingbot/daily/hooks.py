"""The four places the daily loop hands off to code built elsewhere.

`config/daily.toml` names each hook as "module:function". The defaults in
`swingbot.daily.stubs` do nothing harmful and say what is missing, so the loop
runs end to end before every piece exists. Swap a line in the config to plug
the real step in; the loop itself does not change.

Contracts (every hook takes the `Day` first; see `loop.Day` for its fields):

    refresh(day) -> Step
        Bring prices and news up to `day.as_of`: saved CSVs under
        `day.data.prices_dir` and the events file `day.data.events_file`.
        Runs first, before anything reads data.

    broker_settle(day) -> Step
        Settle what the paper broker holds, using only sessions that have
        closed by `day.as_of`: fill or lapse yesterday's DAY entries, and fire
        OCO stops and targets on held positions. Every fill goes into the
        ledger with `Ledger(day.ledger).append(...)` in the same shape
        `swingbot-paper fill` writes (keep `fingerprint` on entry fills, so the
        Verifier can link the trade to its news). Runs before the account is
        replayed, so today's checks see the settled account.

    signal_to_order(day, signals, portfolio, mood) -> list[Order]
        Turn today's swarm signals (dicts: event_id, ticker, published_at,
        headline, sentiment, confidence, score, rationale) into sized orders
        for the gate. `portfolio` is the replayed paper account, `mood` the
        `swingbot-risk mood` result or None. Long only, CNC, with stop and
        target. Return [] for no trades. The loop gates every order; a hook
        never decides PASS itself.

    broker_submit(day, tickets) -> Step
        Hand today's tickets (from `swingbot-risk ticket`, all PASSed today) to
        the paper broker. Nothing may be sent to a real broker.
"""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass

HOOKS = ("refresh", "broker_settle", "signal_to_order", "broker_submit")

OK = "ok"
STUB = "stub"          # the hook is a placeholder: nothing was done
SKIPPED = "skipped"    # the loop chose not to run this step today
BLOCKED = "blocked"    # a rule stopped it
FAILED = "failed"


@dataclass
class Step:
    name: str
    status: str
    detail: str

    def to_json(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        return f"{self.status.upper():8} {self.name}: {self.detail}"


class HookError(Exception):
    """A hook could not do its job. The message is one plain sentence."""


def load_hook(spec: str):
    module, sep, name = spec.partition(":")
    if not sep:
        raise ValueError(f"hook {spec!r} must be 'module:function'")
    fn = getattr(importlib.import_module(module), name, None)
    if not callable(fn):
        raise ValueError(f"hook {spec!r}: {module} has no function {name}")
    return fn
