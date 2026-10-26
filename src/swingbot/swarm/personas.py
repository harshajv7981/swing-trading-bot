"""Market participant personas for the swarm (blueprint Layer B)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    name: str
    description: str
    weight: float = 1.0  # share of the crowd this persona's view carries
    # How the offline stub bends the raw news tone. LLM personas ignore these
    # and work from `description` instead.
    upside_gain: float = 1.0
    downside_gain: float = 1.0
    susceptibility: float = 0.3  # how far the persona drifts toward the crowd per round


DEFAULT_PERSONAS: tuple[Persona, ...] = (
    Persona(
        name="Retail Bull",
        description=(
            "An Indian retail trader active on X, Telegram and Moneycontrol forums. "
            "Chases momentum, reacts fast to good headlines, tends to shrug off bad ones."
        ),
        weight=1.0,
        upside_gain=1.4,
        downside_gain=0.7,
        susceptibility=0.5,
    ),
    Persona(
        name="Institutional Bear",
        description=(
            "A sceptical buy-side analyst at a domestic mutual fund. Focuses on "
            "valuation, earnings quality and guidance, and discounts hype."
        ),
        weight=1.5,
        upside_gain=0.6,
        downside_gain=1.3,
        susceptibility=0.15,
    ),
    Persona(
        name="Foreign Institutional Investor",
        description=(
            "An FII desk allocating to Indian equities. Weighs global risk appetite, "
            "INR moves, RBI policy and index weights alongside company news."
        ),
        weight=1.5,
        upside_gain=0.9,
        downside_gain=1.1,
        susceptibility=0.2,
    ),
    Persona(
        name="Financial Media",
        description=(
            "Business TV and financial press (CNBC-TV18, ET Now, Mint). Amplifies "
            "whatever the story is and shapes how retail reads it."
        ),
        weight=0.8,
        upside_gain=1.3,
        downside_gain=1.3,
        susceptibility=0.4,
    ),
)
