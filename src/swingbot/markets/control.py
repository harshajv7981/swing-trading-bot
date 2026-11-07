"""Run namespaces, execution modes and halt files: the control plane shared by both markets.

Layout (docs/parallel-markets-learning-plan.md, "Parallel execution model"):

    runs/markets/<market>/<run-id>/manifest.json   one run, its frozen config
    data/state/markets/<market>/                   that market's mutable state
    data/state/markets/HALT                        global halt: no market sends
    data/state/markets/<market>/HALT               one market's halt

The two markets share no mutable state: each run and each state directory sits
under its own market key, and a run id cannot climb out of its directory.

Modes: paper sends to a paper account; shadow builds orders and never sends;
live is refused in code. Unlocking live is a separate change, approved per
market, after the paper and shadow gates in the plan pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from .profiles import MarketProfile, get_profile

RUNS_ROOT = Path("runs")
STATE_ROOT = Path("data/state")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def utc_stamp(at: datetime) -> str:
    if at.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


# --- modes and halts ------------------------------------------------------------


class ExecutionMode(str, Enum):
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"

    @classmethod
    def parse(cls, value: str) -> ExecutionMode:
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValueError(f"mode must be paper, shadow or live, not {value!r}") from None


class SendRefused(RuntimeError):
    """Nothing may be sent. The message is one plain sentence."""


class Halted(SendRefused):
    """A halt file is in place."""


def _halt_path(market: str | None, state_root: Path) -> Path:
    base = Path(state_root) / "markets"
    return base / "HALT" if market is None else base / get_profile(market).key / "HALT"


def halt(market: str | None, reason: str, state_root: Path = STATE_ROOT) -> Path:
    """Stop one market (or every market when `market` is None) from sending."""
    path = _halt_path(market, state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{utc_stamp(datetime.now(UTC))} {reason.strip() or 'no reason given'}\n")
    return path


def resume(market: str | None, state_root: Path = STATE_ROOT) -> bool:
    path = _halt_path(market, state_root)
    if path.exists():
        path.unlink()
        return True
    return False


def halted(market: str, state_root: Path = STATE_ROOT) -> str | None:
    """The halt reason in force for `market` (global first), or None."""
    for scope in (None, market):
        path = _halt_path(scope, state_root)
        if path.exists():
            who = "all markets" if scope is None else get_profile(market).key
            return f"{who} halted: {path.read_text().strip()}"
    return None


def may_send(market: str, mode: ExecutionMode, state_root: Path = STATE_ROOT) -> None:
    """Raise unless an order for `market` may go out in `mode`. Returns None when it may."""
    reason = halted(market, state_root)
    if reason:
        raise Halted(f"{reason}; remove the halt file to resume.")
    if mode is ExecutionMode.SHADOW:
        raise SendRefused("shadow mode builds the orders and reconciles, but never sends them.")
    if mode is ExecutionMode.LIVE:
        raise SendRefused(f"live orders for {get_profile(market).key} are blocked in code until the paper and "
                          "shadow gates pass and a separate change unlocks them.")


# --- run namespaces -----------------------------------------------------------------


@dataclass(frozen=True)
class RunSpace:
    profile: MarketProfile
    run_id: str
    runs_root: Path = RUNS_ROOT
    state_root: Path = STATE_ROOT

    @property
    def run_dir(self) -> Path:
        return Path(self.runs_root) / "markets" / self.profile.key / self.run_id

    @property
    def state_dir(self) -> Path:
        return Path(self.state_root) / "markets" / self.profile.key

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    @staticmethod
    def _check(run_id: str) -> str:
        if not _RUN_ID.match(run_id or ""):
            raise ValueError(f"run id {run_id!r} must be letters, digits, '.', '_' or '-', not a path")
        return run_id

    @classmethod
    def create(cls, market: str, run_id: str, runs_root: Path = RUNS_ROOT, state_root: Path = STATE_ROOT,
               config: dict | None = None, now: datetime | None = None) -> RunSpace:
        space = cls(get_profile(market), cls._check(run_id), Path(runs_root), Path(state_root))
        if space.run_dir.exists():
            raise FileExistsError(f"run {space.profile.key}/{run_id} already exists; a run's config is frozen")
        space.run_dir.mkdir(parents=True)
        space.state_dir.mkdir(parents=True, exist_ok=True)
        p = space.profile
        manifest = {"market": p.key, "profile": p.name, "currency": p.currency, "timezone": str(p.timezone),
                    "broker": p.broker, "run_id": run_id,
                    "created_utc": utc_stamp(now or datetime.now(UTC)), "config": config or {}}
        space.manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return space

    @classmethod
    def open(cls, market: str, run_id: str, runs_root: Path = RUNS_ROOT,
             state_root: Path = STATE_ROOT) -> RunSpace:
        space = cls(get_profile(market), cls._check(run_id), Path(runs_root), Path(state_root))
        if not space.manifest_path.exists():
            raise FileNotFoundError(f"no run {space.profile.key}/{run_id} under {runs_root}")
        return space

    def manifest(self) -> dict:
        return json.loads(self.manifest_path.read_text())
