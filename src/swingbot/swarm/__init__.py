from .base import SwarmEngine
from .mirofish import MiroFishEngine
from .persona_swarm import PersonaSwarm, ReportAgent
from .personas import DEFAULT_PERSONAS, Persona
from .reasoners import ClaudeReasoner, LexiconReasoner, PersonaView
from .recorded import MissingViews, RecordedSwarm


def build_engine(
    name: str, *, rounds: int = 2, horizon: str = "72h", model: str | None = None, views: str | None = None
) -> SwarmEngine:
    """Engine factory used by the CLI."""
    if name == "lexicon":
        return PersonaSwarm(LexiconReasoner(), rounds=rounds, horizon=horizon)
    if name == "claude":
        reasoner = ClaudeReasoner(model=model) if model else ClaudeReasoner()
        return PersonaSwarm(reasoner, rounds=rounds, horizon=horizon)
    if name == "recorded":
        if not views:
            raise ValueError("--engine recorded needs --views <file>")
        return RecordedSwarm(views, horizon=horizon)
    if name == "mirofish":
        return MiroFishEngine(horizon=horizon)
    raise ValueError(f"unknown engine {name!r} (choose lexicon, claude, recorded or mirofish)")


__all__ = [
    "SwarmEngine", "PersonaSwarm", "ReportAgent", "Persona", "DEFAULT_PERSONAS",
    "PersonaView", "LexiconReasoner", "ClaudeReasoner", "MiroFishEngine", "RecordedSwarm", "MissingViews", "build_engine",
]
