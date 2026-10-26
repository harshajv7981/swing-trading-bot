"""Persona "brains": given a persona, a news event and what the crowd said last
round, produce that persona's view.

LexiconReasoner is a deterministic offline stub, so the pipeline runs and is
testable with no API key. ClaudeReasoner asks Claude to role-play the persona.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Protocol

from ..models import NewsEvent
from .personas import Persona


@dataclass(frozen=True)
class PersonaView:
    persona: str
    stance: float  # -1 very bearish .. +1 very bullish
    conviction: float  # 0..1
    rationale: str = ""


class PersonaReasoner(Protocol):
    def react(
        self, persona: Persona, event: NewsEvent, crowd: list[PersonaView] | None
    ) -> PersonaView: ...


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------- #
# Offline stub
# --------------------------------------------------------------------------- #

POSITIVE = {
    "beat": 1.0, "beats": 1.0, "record": 0.8, "surge": 1.0, "surges": 1.0,
    "jump": 0.8, "jumps": 0.8, "upgrade": 1.0, "upgraded": 1.0, "wins": 0.8,
    "win": 0.6, "order": 0.4, "orders": 0.4, "contract": 0.5, "approval": 0.8,
    "approved": 0.8, "growth": 0.5, "profit": 0.4, "raises": 0.6, "buyback": 0.9,
    "dividend": 0.4, "expansion": 0.5, "strong": 0.6, "outperform": 0.8,
    "acquire": 0.3, "acquisition": 0.3, "launch": 0.3, "rally": 0.8, "higher": 0.4,
}
NEGATIVE = {
    "miss": 1.0, "misses": 1.0, "missed": 1.0, "loss": 0.8, "losses": 0.8,
    "plunge": 1.0, "plunges": 1.0, "fall": 0.6, "falls": 0.6, "decline": 0.6,
    "downgrade": 1.0, "downgraded": 1.0, "probe": 0.9, "raid": 1.0, "penalty": 0.8,
    "fine": 0.5, "fraud": 1.2, "default": 1.2, "resigns": 0.8, "resignation": 0.8,
    "cut": 0.6, "cuts": 0.6, "weak": 0.6, "slump": 0.9, "ban": 0.9, "warning": 0.7,
    "lower": 0.4, "pledge": 0.6, "sebi": 0.4, "underperform": 0.8, "delay": 0.5,
}
NEGATORS = {"not", "no", "fails", "without"}

_TOKEN = re.compile(r"[a-z]+")


def news_tone(text: str) -> float:
    """Crude signed tone in [-1, 1]. Headline words count double."""
    tokens = _TOKEN.findall(text.lower())
    score, hits = 0.0, 0
    for i, tok in enumerate(tokens):
        w = POSITIVE.get(tok, 0.0) - NEGATIVE.get(tok, 0.0)
        if w == 0.0:
            continue
        if i > 0 and tokens[i - 1] in NEGATORS:
            w = -w
        score += w
        hits += 1
    if hits == 0:
        return 0.0
    return _clip(score / (hits + 1.0), -1.0, 1.0)


class LexiconReasoner:
    """Keyword tone, bent by each persona's biases, then herding toward the crowd."""

    name = "lexicon"

    def react(self, persona, event, crowd):
        tone = news_tone(f"{event.headline} {event.headline} {event.body}")
        gain = persona.upside_gain if tone >= 0 else persona.downside_gain
        stance = _clip(tone * gain, -1.0, 1.0)
        conviction = _clip(abs(tone) * gain, 0.05, 1.0)

        if crowd:
            others = [v for v in crowd if v.persona != persona.name]
            if others:
                crowd_mean = sum(v.stance for v in others) / len(others)
                s = persona.susceptibility
                stance = (1 - s) * stance + s * crowd_mean

        return PersonaView(
            persona=persona.name,
            stance=round(stance, 4),
            conviction=round(conviction, 4),
            rationale=f"lexicon tone {tone:+.2f}",
        )


# --------------------------------------------------------------------------- #
# Claude-backed persona
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are role-playing one participant in a simulated Indian equity market.
You will be shown a single news item about an NSE-listed stock, and possibly how
other participants reacted. React the way your persona would over the next
48 to 72 hours of trading.

Judge only from the text you are given and general market knowledge. Do not use
any memory of how this stock actually traded after the news date: this is a
historical backtest and recalling the real outcome would invalidate it.

Return:
- stance: -1.0 (strongly expect the stock to fall) to 1.0 (strongly expect it to rise)
- conviction: 0.0 to 1.0, how sure you are
- rationale: one or two sentences, in your persona's voice"""


class ClaudeReasoner:
    """Each persona is one Claude call with structured output.

    Cost scales with events x personas x rounds, so start with a small event file.
    """

    name = "claude"

    def __init__(self, model: str = "claude-opus-5-5", client=None, max_tokens: int = 4000):
        # Optional dependency: pip install .[llm]
        import anthropic
        from pydantic import BaseModel, Field

        class _View(BaseModel):
            stance: float = Field(ge=-1.0, le=1.0)
            conviction: float = Field(ge=0.0, le=1.0)
            rationale: str

        self._View = _View
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def _prompt(self, persona: Persona, event: NewsEvent, crowd) -> str:
        parts = [
            f"Your persona: {persona.name}. {persona.description}",
            "",
            f"Stock: {event.ticker} (NSE)",
            f"Published: {event.published_at.isoformat()}",
            f"Headline: {event.headline}",
        ]
        if event.body:
            parts.append(f"Details: {event.body}")
        if crowd:
            parts.append("\nHow other participants reacted last round:")
            parts.extend(
                f"- {v.persona}: stance {v.stance:+.2f}, conviction {v.conviction:.2f}. {v.rationale}"
                for v in crowd
                if v.persona != persona.name
            )
        return "\n".join(parts)

    def react(self, persona, event, crowd):
        response = self.client.beta.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._prompt(persona, event, crowd)}],
            output_format=self._View,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            # Treat a decline as "no view" rather than crashing the whole run.
            return PersonaView(persona.name, 0.0, 0.0, f"no view ({response.stop_reason})")
        v = response.parsed_output
        return PersonaView(persona.name, v.stance, v.conviction, v.rationale)


def view_to_dict(v: PersonaView) -> dict:
    return asdict(v)
