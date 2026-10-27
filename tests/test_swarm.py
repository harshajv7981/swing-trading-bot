from datetime import datetime
from types import SimpleNamespace

import pytest

from swingbot.models import IST, NewsEvent, Sentiment
from swingbot.swarm import ClaudeReasoner, LexiconReasoner, PersonaSwarm, build_engine
from swingbot.swarm.reasoners import news_tone


def ev(headline):
    return NewsEvent("t", "ABC", datetime(2026, 1, 5, 8, tzinfo=IST), headline)


def test_news_tone_direction():
    assert news_tone("Company beats estimates and raises guidance") > 0
    assert news_tone("SEBI probe after profit misses estimates") < 0
    assert news_tone("Board meeting on Tuesday") == 0
    assert news_tone("Company does not miss deadline") > 0  # negation flips


def test_lexicon_swarm_signals():
    engine = build_engine("lexicon")
    bull = engine.simulate(ev("DemoCo wins record order, analysts upgrade"))
    bear = engine.simulate(ev("DemoCo CFO resigns amid fraud probe"))
    flat = engine.simulate(ev("DemoCo to hold annual general meeting"))
    assert bull.sentiment is Sentiment.BULLISH
    assert bear.sentiment is Sentiment.BEARISH
    assert flat.sentiment is Sentiment.NEUTRAL
    assert 0 <= bull.confidence <= 1
    assert len(bull.persona_views) == 4


def test_rounds_pull_personas_together():
    one = PersonaSwarm(LexiconReasoner(), rounds=1).simulate(ev("DemoCo beats estimates"))
    three = PersonaSwarm(LexiconReasoner(), rounds=3).simulate(ev("DemoCo beats estimates"))
    spread = lambda s: max(v["stance"] for v in s.persona_views) - min(v["stance"] for v in s.persona_views)
    assert spread(three) < spread(one)


class FakeClient:
    """Stands in for anthropic.Anthropic so no API call is made."""

    def __init__(self, reasoner):
        self.prompts = []
        self.reasoner = reasoner
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        view = self.reasoner._View(stance=0.6, conviction=0.7, rationale="looks good")
        return SimpleNamespace(stop_reason="end_turn", parsed_output=view)


needs_anthropic = pytest.mark.skipif(
    __import__("importlib").util.find_spec("anthropic") is None,
    reason="the optional anthropic package is not installed (pip install -e '.[llm]')")


@needs_anthropic
def test_claude_reasoner_with_fake_client():
    r = ClaudeReasoner(client=object())
    r.client = FakeClient(r)
    sig = PersonaSwarm(r, rounds=2).simulate(ev("DemoCo beats estimates"))
    assert sig.sentiment is Sentiment.BULLISH
    assert len(r.client.prompts) == 8  # 4 personas x 2 rounds
    assert "other participants reacted" in r.client.prompts[-1]
    assert "other participants reacted" not in r.client.prompts[0]


@needs_anthropic
def test_claude_reasoner_refusal_is_no_view():
    r = ClaudeReasoner(client=object())
    r.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        parse=lambda **kw: SimpleNamespace(stop_reason="refusal", parsed_output=None))))
    v = r.react(PersonaSwarm(r).personas[0], ev("x"), None)
    assert v.stance == 0.0 and v.conviction == 0.0
