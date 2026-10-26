"""The risk limits, read from config/risk.toml. Nothing in the bot writes them."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

DEFAULT_CONFIG = Path("config/risk.toml")


@dataclass(frozen=True)
class RiskConfig:
    risk_pct: float = 1.0
    hard_max_risk_pct: float = 2.0
    gap_allowance_pct: float = 0.5
    max_position_pct: float = 15.0
    min_reward_risk: float = 1.0
    cash_buffer_pct: float = 0.0
    max_open_positions: int = 5
    max_per_sector: int = 2
    max_open_risk_pct: float = 5.0
    allow_adding: bool = False
    weekly_kill_pct: float = 5.0
    daily_loss_pct: float = 2.0
    trend_sma: int = 200
    min_traded_value_cr: float = 10.0
    results_blackout_sessions: int = 2
    require_results_date: bool = False
    defensive_risk_scale: float = 0.5
    atr_period: int = 14
    min_stop_atr: float = 0.5
    trail_atr: float = 2.0
    tick: float = 0.05

    def __post_init__(self) -> None:
        if not 0 < self.risk_pct <= self.hard_max_risk_pct:
            raise ValueError(
                f"risk_pct {self.risk_pct} must be above 0 and at most hard_max_risk_pct "
                f"{self.hard_max_risk_pct}"
            )
        if self.hard_max_risk_pct > 2.0:
            raise ValueError(f"hard_max_risk_pct {self.hard_max_risk_pct} is over the build plan's 2% cap")
        if not 0 <= self.defensive_risk_scale <= 1:
            raise ValueError(f"defensive_risk_scale {self.defensive_risk_scale} must be between 0 and 1")
        if self.gap_allowance_pct < 0 or self.tick <= 0:
            raise ValueError("gap_allowance_pct must be >= 0 and tick > 0")

    @classmethod
    def load(cls, path: str | Path | None = None) -> "RiskConfig":
        """Read the TOML; with no path, the repo default if present, else built-in defaults."""
        path = Path(path) if path else DEFAULT_CONFIG
        if not path.exists():
            if path != DEFAULT_CONFIG:
                raise FileNotFoundError(f"no risk config at {path}")
            return cls()
        flat: dict = {}
        for section in tomllib.loads(path.read_text()).values():
            flat.update(section)
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(flat) - known)
        if unknown:
            raise ValueError(f"unknown risk settings in {path}: {', '.join(unknown)}")
        return cls(**flat)

    def risk_pct_for(self, mood_verdict: str | None) -> float:
        """R2's limit for a new entry: cut by `defensive_risk_scale` when the market mood is defensive."""
        if mood_verdict == "defensive":
            return self.risk_pct * self.defensive_risk_scale
        return self.risk_pct
