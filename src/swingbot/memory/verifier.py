"""The Verifier: review each closed trade and write one lesson about it.

As with the swarm, Claude Code plays the Verifier itself (see
.claude/skills/trade-verifier/), so there is no API key. `review_brief` writes
the trades to review; Claude Code appends one JSON line per trade to a lessons
file; `load_lessons` reads it back for `MemoryStore.add_lesson`:

    {"trade_id": "base:e01", "verdict": "wrong",
     "lesson": "Record orders in low-margin segments faded within two sessions ...",
     "tags": ["order win", "infra", "fade"]}
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from .store import VERDICTS, Lesson

VERIFIER_PROMPT = f"""You are the Verifier. For each closed trade below, the swarm's reasoning and the
real outcome are shown. Decide why the call worked or failed, then write one
lesson the swarm can use the next time similar news breaks.

- verdict: one of {", ".join(VERDICTS)}. "right_for_wrong_reason" means the stock
  moved the way the swarm said, but not for the reason it gave (for example it
  rode the whole market). Use "no_call" when the swarm stayed neutral.
- lesson: one or two sentences, a rule of thumb about this kind of news, stock
  or setup, written so it still makes sense without this trade in front of you.
  Name the pattern ("promoter pledge news", "beat on profit but missed on
  margins"), not this company's story.
- tags: two to five short words for the kind of news and sector.

Judge each trade only from its own record below. Don't carry over what you saw
in later trades, and don't use anything you remember about these companies."""


def load_views(path: str | Path) -> dict[str, dict]:
    """Final-round persona views and the debate per event, from a swarm views file."""
    personas: dict[str, dict[int, dict[str, dict]]] = defaultdict(lambda: defaultdict(dict))
    debate: dict[str, dict[str, str]] = defaultdict(dict)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        eid = str(r["event_id"])
        role = r.get("role")
        if role in ("bull", "bear"):
            debate[eid][role] = r.get("argument", "")
        elif role == "judge":
            debate[eid]["judge"] = r.get("rationale", "")
        elif role is None and "persona" in r:
            personas[eid][int(r.get("round", 1))][r["persona"]] = {
                "persona": r["persona"], "stance": r.get("stance"), "rationale": r.get("rationale", "")}
    out = {}
    for eid in set(personas) | set(debate):
        rounds = personas.get(eid, {})
        out[eid] = {
            "persona_views": list(rounds[max(rounds)].values()) if rounds else [],
            "debate": debate.get(eid, {}),
        }
    return out


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def _trade_line(t, trade: dict) -> str:
    """The bot is long-only: say what it actually did, apart from whether the call was right."""
    if not trade:
        return f"Return after costs {_pct(t['trade_ret'])}."
    if str(trade.get("traded")).lower() != "true":
        return f"No trade ({trade.get('no_trade_reason', 'no call')})."
    return (f"Bot bought at {trade['trade_entry']:.2f}, stop {trade['trade_stop']:.2f}, "
            f"target {trade['trade_target']:.2f}; out on {trade['trade_exit_date']} at "
            f"{trade['trade_exit_price']:.2f} ({trade['exit_reason']}), {_pct(t['trade_ret'])} after costs.")


def review_brief(trades: list) -> str:
    lines = ["# Verifier brief", "", VERIFIER_PROMPT, "", f"## Closed trades ({len(trades)})", ""]
    for t in trades:
        extra = json.loads(t["extra"] or "{}")
        hit = {1: "right", 0: "wrong"}.get(t["hit"], "no call")
        lines += [
            f"### {t['trade_id']} · {t['ticker']} · news {t['published_at']}",
            f"Headline: {t['headline']}",
            f"Swarm call: {t['sentiment']} (confidence {t['confidence']}, score {t['score']})",
            f"Swarm rationale: {t['rationale']}",
        ]
        for v in extra.get("persona_views", []):
            lines.append(f"- {v['persona']} ({v['stance']}): {v['rationale']}")
        for role in ("bull", "bear", "judge"):
            if extra.get("debate", {}).get(role):
                lines.append(f"- {role.title()}: {extra['debate'][role]}")
        lines += [
            f"Outcome: held {t['entry_date']} to {t['exit_date']}, stock {_pct(t['ret'])}, "
            f"benchmark {_pct(t['bench_ret'])}, best {_pct(t['max_up'])}, worst {_pct(t['max_down'])}. "
            f"Call was {hit}. {_trade_line(t, extra.get('trade', {}))}",
            "",
        ]
    return "\n".join(lines)


def load_lessons(path: str | Path) -> list[Lesson]:
    lessons = []
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            lessons.append(Lesson(
                trade_id=str(r["trade_id"]),
                verdict=str(r["verdict"]),
                lesson=str(r["lesson"]),
                tags=tuple(str(t) for t in r.get("tags", [])),
            ))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"{path}:{n}: bad lesson line ({e})") from e
    return lessons
