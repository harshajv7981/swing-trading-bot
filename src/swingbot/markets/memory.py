"""Step 7: episode records, the mistake taxonomy and market-scoped memory.

From docs/parallel-markets-learning-plan.md ("Memory and learning design"):

- **Episodes**: one per decision. For the momentum trials that is a month-end
  booking: what was visible (signal date, information cutoff, universe, data
  notes), what was believed (the picks should beat the main benchmark next
  month), what was intended (orders), what the broker did, and, once the next
  month is booked, what happened (return, benchmark, excess) and how it is
  classified.
- **Mistakes**: the plan's ten categories. The automatic classifier names only
  what the record proves (broker/API error, execution error, risk-policy
  violation, or a loss with a clean process). The judgement categories (forecast,
  regime, calibration, cutoff, universe, a reused market assumption) are added
  only by a reviewer, with evidence.
- **Memory**: lessons in three stores, `nse`, `us` and `shared`. A market lesson
  cites only its own market's episodes. A shared lesson needs evidence from both
  markets, or a stated reason why it is market-neutral. Anyone (or the auto rule:
  the same process mistake in two episodes) may propose. Only a named person may
  approve, reject or retire.

Everything is append-only JSON lines under `<state>/markets/<scope>/memory/`;
reading folds the events. Re-running an update appends nothing new.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from .control import STATE_ROOT
from .trials import NSE_MAIN, US_MAIN, TrialMonth

SCOPES = ("nse", "us", "shared")
LEVELS = ("market", "strategy", "shared")
CONFIDENCE = ("low", "medium", "high")
NOT_A_PERSON = {"", "auto", "agent", "claude", "bot", "system"}
MAIN = {"nse": NSE_MAIN, "us": US_MAIN}


class MemoryRefused(ValueError):
    """The memory will not take this. The message is one plain sentence."""


class Mistake(str, Enum):
    LEAKAGE = "data leakage or incorrect cutoff"
    UNIVERSE = "bad universe membership"
    FORECAST = "forecast error"
    REGIME = "regime mismatch"
    CALIBRATION = "overconfidence or poor uncertainty calibration"
    EXECUTION = "execution error"
    BROKER = "broker/API error"
    RISK = "risk-policy violation"
    MARKET_ASSUMPTION_REUSED = "market-specific assumption incorrectly reused"
    EXPECTED_LOSS = "expected loss with correct process"

    @classmethod
    def judgment_only(cls) -> set[Mistake]:
        return {cls.LEAKAGE, cls.UNIVERSE, cls.FORECAST, cls.REGIME, cls.CALIBRATION, cls.MARKET_ASSUMPTION_REUSED}


_BROKER = ("rejected", "alpaca holds", "dhan", "broker")
_EXECUTION = ("not filled", "no close", "the last one", "valued at")


def classify(tm: TrialMonth, excess: float | None) -> tuple[list[str], list[str]]:
    """The mistakes a settled month's own record proves, with the evidence for each."""
    found, evidence = set(), []
    for a in tm.anomalies:
        low = a.lower()
        if any(k in low for k in _BROKER):
            found.add(Mistake.BROKER)
            evidence.append(a)
        elif any(k in low for k in _EXECUTION):
            found.add(Mistake.EXECUTION)
            evidence.append(a)
    if tm.exposure is not None and tm.exposure > 1.0 + 1e-9:
        found.add(Mistake.RISK)
        evidence.append(f"invested {tm.exposure:.0%} of equity")
    if not found and excess is not None and excess < 0:
        found.add(Mistake.EXPECTED_LOSS)
        evidence.append(f"behind the benchmark by {-excess:.2%} with a clean process")
    return [m.value for m in Mistake if m in found], evidence


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Memory:
    def __init__(self, state_root: Path = STATE_ROOT):
        self.root = Path(state_root)

    def _path(self, scope: str, name: str) -> Path:
        if scope not in SCOPES:
            raise MemoryRefused(f"scope must be one of {', '.join(SCOPES)}, not {scope!r}")
        return self.root / "markets" / scope / "memory" / f"{name}.jsonl"

    def _append(self, scope: str, name: str, rec: dict) -> None:
        path = self._path(scope, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"at_utc": _now(), **rec}, sort_keys=True) + "\n")

    def _read(self, scope: str, name: str) -> list[dict]:
        path = self._path(scope, name)
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []

    # --- episodes ----------------------------------------------------------------------------------

    def episodes(self, market: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in self._read(market, "episodes"):
            ep = out.setdefault(e["episode"], {"mistakes": [], "evidence": []})
            if e["kind"] == "episode_opened":
                ep.update({k: v for k, v in e.items() if k not in ("kind", "at_utc")}, status="open")
            elif e["kind"] == "episode_settled":
                ep.update(status="settled", realized=e["realized"], bench=e["bench"], excess=e["excess"])
                ep["mistakes"] += [m for m in e["mistakes"] if m not in ep["mistakes"]]
                ep["evidence"] += e["evidence"]
            elif e["kind"] == "episode_judged":
                if e["mistake"] not in ep["mistakes"]:
                    ep["mistakes"].append(e["mistake"])
                ep["evidence"].append(f"{e['by']}: {e['evidence']}")
        return out

    def update(self, market: str, rows: list[TrialMonth]) -> int:
        """Open an episode for each booked month and settle it when the next month is booked."""
        known, added = self.episodes(market), 0
        for i, tm in enumerate(rows):
            eid = f"{market}-{tm.strategy}-{tm.month}"
            if eid not in known:
                self._append(market, "episodes", {
                    "kind": "episode_opened", "episode": eid, "market": market, "strategy": tm.strategy,
                    "version": tm.version, "month": tm.month, "asof": tm.asof, "info_cutoff_utc": tm.info_cutoff_utc,
                    "visible": {"universe_size": tm.universe_size, "anomalies": tm.anomalies},
                    "belief": {"picks": tm.picks, "expect": f"beat {MAIN[market]} over the next month"},
                    "intended": {"orders": tm.orders[0], "filled": tm.orders[1], "rejected": tm.orders[2]}})
                added += 1
            if i + 1 < len(rows) and known.get(eid, {}).get("status") != "settled":
                nxt, main = rows[i + 1], MAIN[market]
                b0, b1 = tm.bench.get(main) or 0.0, nxt.bench.get(main)
                bench = (1 + b1) / (1 + b0) - 1 if b1 is not None else None
                excess = nxt.month_return - bench if bench is not None else None
                mistakes, evidence = classify(tm, excess)
                self._append(market, "episodes", {
                    "kind": "episode_settled", "episode": eid, "realized": nxt.month_return, "bench": bench,
                    "excess": excess, "mistakes": mistakes, "evidence": evidence, "by": "auto"})
                added += 1
        self._auto_propose(market)
        return added

    def classify(self, episode: str, mistake: Mistake, evidence: str, by: str) -> None:
        if not evidence.strip():
            raise MemoryRefused("a judgement needs evidence: say what in the record shows it")
        market = episode.split("-", 1)[0]
        if episode not in self.episodes(market):
            raise MemoryRefused(f"no episode {episode} in the {market} memory")
        self._append(market, "episodes", {"kind": "episode_judged", "episode": episode,
                                          "mistake": Mistake(mistake).value, "evidence": evidence, "by": by})

    # --- lessons -------------------------------------------------------------------------------------

    def lessons(self, scope: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in self._read(scope, "lessons"):
            if e["kind"] == "lesson_proposed":
                out[e["lesson"]] = {**{k: v for k, v in e.items() if k not in ("kind", "at_utc")},
                                    "status": "proposed", "history": [f"{e['at_utc']} proposed by {e['proposed_by']}"]}
            else:
                rec = out[e["lesson"]]
                rec["status"] = e["kind"].removeprefix("lesson_")
                rec["history"].append(f"{e['at_utc']} {rec['status']} by {e['by']}: {e['why']}")
        return out

    def propose(self, scope: str, level: str, text: str, evidence: list[str], confidence: str, by: str,
                strategy: str | None = None, market_neutral: str | None = None, auto_key: str | None = None) -> str:
        if confidence not in CONFIDENCE:
            raise MemoryRefused(f"confidence must be low, medium or high, not {confidence!r}")
        if level not in LEVELS or (level == "shared") != (scope == "shared"):
            raise MemoryRefused(f"a {scope} lesson is a market or strategy lesson; only shared lessons are shared")
        markets = set()
        for eid in evidence:
            market = eid.split("-", 1)[0]
            if scope != "shared" and market != scope:
                raise MemoryRefused(f"a {scope} lesson can only cite {scope} episodes; {eid} is not one")
            if market not in ("nse", "us") or eid not in self.episodes(market):
                raise MemoryRefused(f"no episode {eid} to cite")
            markets.add(market)
        if scope == "shared" and markets != {"nse", "us"} and not market_neutral:
            raise MemoryRefused("a shared lesson needs evidence from both markets, or a market_neutral reason")
        if scope != "shared" and not evidence:
            raise MemoryRefused("a market lesson needs at least one episode as evidence")
        lid = f"{scope}-L{len(self.lessons(scope)) + 1:04d}"
        self._append(scope, "lessons", {
            "kind": "lesson_proposed", "lesson": lid, "scope": scope, "level": level, "strategy": strategy,
            "text": text, "evidence": sorted(evidence), "confidence": confidence, "proposed_by": by,
            "market_neutral": market_neutral, "auto_key": auto_key})
        return lid

    def decide(self, scope: str, lesson: str, action: str, by: str, why: str) -> None:
        if by.strip().lower() in NOT_A_PERSON:
            raise MemoryRefused("only a person may approve, reject or retire a lesson; the agent may only propose")
        if not why.strip():
            raise MemoryRefused("say why")
        rec = self.lessons(scope).get(lesson)
        allowed = {"approve": "proposed", "reject": "proposed", "retire": "approved"}
        if rec is None or action not in allowed or rec["status"] != allowed[action]:
            raise MemoryRefused(f"cannot {action} {lesson}: it is {rec['status'] if rec else 'not there'}")
        self._append(scope, "lessons", {"kind": f"lesson_{action}d", "lesson": lesson, "by": by, "why": why})

    def _auto_propose(self, market: str) -> None:
        """The same process mistake in two settled episodes: propose a low-confidence market lesson, once."""
        eps = self.episodes(market)
        counts = Counter(m for ep in eps.values() for m in ep["mistakes"] if m != Mistake.EXPECTED_LOSS.value)
        keys = {v.get("auto_key") for v in self.lessons(market).values()}
        for mistake, n in sorted(counts.items()):
            key = f"repeated:{mistake}"
            if n >= 2 and key not in keys:
                ids = sorted(e for e, ep in eps.items() if mistake in ep["mistakes"])
                self.propose(market, "market", f"Repeated {mistake} in {n} episodes: review the process behind it.",
                             ids, "low", by="auto", auto_key=key)


def show(mem: Memory, market: str) -> str:
    eps, lessons = mem.episodes(market), mem.lessons(market)
    lines = [f"# {market.upper()} memory", "", "| episode | status | realized | benchmark | excess | mistakes |",
             "|---|---|---|---|---|---|"]

    def pct(x):
        return "" if x is None else f"{x * 100:+.1f}%"

    for eid, ep in sorted(eps.items()):
        lines.append(f"| {eid} | {ep.get('status')} | {pct(ep.get('realized'))} | {pct(ep.get('bench'))} | "
                     f"{pct(ep.get('excess'))} | {'; '.join(ep['mistakes'])} |")
    lines += ["", "| lesson | status | confidence | text | evidence |", "|---|---|---|---|---|"]
    for lid, rec in sorted({**lessons, **mem.lessons("shared")}.items()):
        lines.append(f"| {lid} | {rec['status']} | {rec['confidence']} | {rec['text']} | {', '.join(rec['evidence'])} |")
    return "\n".join(lines) + "\n"
