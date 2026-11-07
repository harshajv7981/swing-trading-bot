"""Step 8: versioned skills per market, candidates from evidence, replay and approval gates.

A skill here is a versioned set of strategy settings (look-back, skip, number of
stocks, band, trend gate), kept in one registry per market, as the memory is:
`<state>/markets/<nse|us>/skills/registry.jsonl`, append-only. Each version
records its owner, reason, evidence, replays, shadow months and status
history: candidate -> shadow -> approved -> retired (or rejected).

- **Baselines:** each market's running trial rule is version 1, approved by its
  pre-registration (US M12, NSE M6).
- **Candidates** come from evidence only (a study's pre-registered answer, a
  lesson, an episode), never from searching settings. `generate_from_study`
  turns the phase 1 study's answers into candidates once.
- **Replay gate (US):** the candidate and the approved version run on the same
  frozen panel with point-in-time members. To pass, the candidate needs a higher
  compound annual return, a worst fall no more than 5 points deeper, and a
  better return in at least 60% of calendar years. The panel was already seen,
  so passing only allows a shadow run.
- **Shadow gate:** at least 6 forward months recorded in shadow (no orders).
- **People decide:** every status change needs a named person. Approving a new
  version retires the old one. Nothing here changes a running pre-registered
  trial: putting an approved version to work needs its own pre-registration.
- NSE replay is not supported yet: the NSE panel lives only on the Mac and its
  universe rule (liquidity ranks 101-250) is not wired into the replay.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .control import STATE_ROOT
from .memory import NOT_A_PERSON

DEFAULTS = {
    "us": {"lookback": 12, "skip": 1, "n": 20, "band": 0.25, "trend_gate": False},
    "nse": {"lookback": 6, "skip": 1, "n": 20, "band": 0.0, "trend_gate": False},
}
BASELINE = {
    "us": ("us-momentum", "docs/research/us-momentum-prereg.md"),
    "nse": ("nse-momentum", "docs/research/momentum-paper-trial.md"),
}
SHADOW_MONTHS = 6
MAX_EXTRA_FALL = 0.05
MIN_YEAR_SHARE = 0.6


class SkillRefused(ValueError):
    """The registry will not take this. The message is one plain sentence."""


def gate(cand: dict, cur: dict, years_won: int, years: int) -> tuple[str, list[str]]:
    why = []
    if cand["cagr"] <= cur["cagr"]:
        why.append(f"no more return ({cand['cagr']:+.1%} against {cur['cagr']:+.1%} a year)")
    if cand["max_dd"] < cur["max_dd"] - MAX_EXTRA_FALL:
        why.append(f"worst fall {cand['max_dd']:.1%} is more than 5 points deeper than {cur['max_dd']:.1%}")
    if years_won < MIN_YEAR_SHARE * years:
        why.append(f"better in only {years_won} of {years} calendar years (needs {MIN_YEAR_SHARE:.0%})")
    return ("fail" if why else "pass"), why


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Skills:
    def __init__(self, state_root: Path = STATE_ROOT):
        self.root = Path(state_root)

    def _path(self, market: str) -> Path:
        if market not in DEFAULTS:
            raise SkillRefused(f"skills are kept separate per market: nse or us, not {market!r}")
        return self.root / "markets" / market / "skills" / "registry.jsonl"

    def _append(self, market: str, rec: dict) -> None:
        path = self._path(market)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"at_utc": _now(), **rec}, sort_keys=True) + "\n")

    def _events(self, market: str) -> list[dict]:
        path = self._path(market)
        if not path.exists() or not path.read_text().strip():
            name, doc = BASELINE[market]
            self._append(market, {"kind": "skill_registered", "skill": f"{name}@v1", "name": name, "version": 1,
                                  "params": DEFAULTS[market], "owner": "Harsha", "status": "approved",
                                  "approved_by": f"pre-registration {doc}", "reason": "the running paper trial's rule",
                                  "evidence": [doc]})
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]

    def all(self, market: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in self._events(market):
            k = e["kind"]
            if k == "skill_registered":
                out[e["skill"]] = {**{x: v for x, v in e.items() if x not in ("kind", "at_utc")},
                                   "history": [f"{e['at_utc']} registered as {e['status']}"], "replays": [],
                                   "shadow": {}}
            elif k == "skill_replayed":
                out[e["skill"]]["replays"].append({x: v for x, v in e.items() if x != "kind"})
            elif k == "skill_shadow_month":
                out[e["skill"]]["shadow"][e["month"]] = {"ret": e["ret"], "current": e["current"]}
            elif k == "skill_status":
                rec = out[e["skill"]]
                rec["status"] = e["status"]
                rec["history"].append(f"{e['at_utc']} {e['status']} by {e['by']}: {e['why']}")
        return out

    def approved(self, market: str, name: str) -> str | None:
        return next((k for k, v in self.all(market).items() if v["name"] == name and v["status"] == "approved"), None)

    def propose(self, market: str, name: str, params: dict, reason: str, evidence: list[str], owner: str) -> str:
        unknown = set(params) - set(DEFAULTS[market])
        if unknown:
            raise SkillRefused(f"unknown setting(s) {', '.join(sorted(unknown))}; known: {', '.join(DEFAULTS[market])}")
        if not evidence:
            raise SkillRefused("a candidate needs evidence: a study, a lesson or an episode")
        full = {**DEFAULTS[market], **params}
        skills = self.all(market)
        for k, v in skills.items():
            if v["name"] == name and v["params"] == full and v["status"] not in ("retired", "rejected"):
                raise SkillRefused(f"{k} already has the same settings")
        version = 1 + max((v["version"] for v in skills.values() if v["name"] == name), default=0)
        key = f"{name}@v{version}"
        self._append(market, {"kind": "skill_registered", "skill": key, "name": name, "version": version,
                              "params": full, "owner": owner, "status": "candidate", "approved_by": None,
                              "reason": reason, "evidence": list(evidence)})
        return key

    def generate_from_study(self, market: str, study: Path, panel_id: str) -> list[str]:
        """Candidates from the phase 1 study's pre-registered answers, once each."""
        res = json.loads(Path(study).read_text())
        a, w = res["answers"], res.get("whole", {})
        ideas = []
        if a.get("q2_m6_vs_m12") == "M6 higher":
            ideas.append(({"lookback": 6}, f"6-1 led 12-1 in the phase 1 study "
                          f"({w.get('M6', {}).get('cagr', 0):+.1%} against {w.get('M12', {}).get('cagr', 0):+.1%} a year)"))
        if a.get("q3_m12_trend_vs_m12") == "helps":
            ideas.append(({"trend_gate": True}, "the trend filter passed the phase 1 study's test"))
        name, made = BASELINE[market][0], []
        known = [v["params"] for v in self.all(market).values() if v["name"] == name]
        for params, reason in ideas:
            if {**DEFAULTS[market], **params} in known:
                continue
            made.append(self.propose(market, name, params, reason, [f"us-research study {panel_id}"], owner="auto"))
        return made

    def replay(self, market: str, key: str, daily: pd.DataFrame, membership, panel_id: str) -> dict:
        if market != "us":
            raise SkillRefused("NSE replay is not supported yet: its panel and universe rule are not wired in")
        from ..us_momentum import month_ends, replay
        from ..us_research import summary, trend_gate

        skills = self.all(market)
        cand = skills[key]
        cur_key = self.approved(market, cand["name"])
        if cur_key is None:
            raise SkillRefused(f"no approved {cand['name']} to compare with")
        m = month_ends(daily)

        def members(d):
            return membership.at(d.date())

        first = m.index[max(cand["params"]["lookback"], skills[cur_key]["params"]["lookback"], 12)]
        last = m.index[-2]

        def run(p: dict) -> pd.Series:
            g = trend_gate(m["SPY"]) if p["trend_gate"] else None
            rows = replay(m, members, first, last, n=p["n"], band=p["band"], lookback=p["lookback"],
                          skip=p["skip"], gate=g)
            return pd.Series({pd.Timestamp(r["held_to"]): r["ret"] for r in rows})

        c, k = run(cand["params"]), run(skills[cur_key]["params"])
        cs, ks = summary(c), summary(k)
        years = sorted(set(c.index.year))
        won = sum((1 + c[c.index.year == y]).prod() > (1 + k[k.index.year == y]).prod() for y in years)
        verdict, why = gate(cs, ks, int(won), len(years))
        rec = {"kind": "skill_replayed", "skill": key, "against": cur_key, "panel": panel_id, "verdict": verdict,
               "why": why, "years": len(years), "years_won": int(won),
               "candidate": {x: cs[x] for x in ("cagr", "max_dd", "sharpe", "vol")},
               "current": {x: ks[x] for x in ("cagr", "max_dd", "sharpe", "vol")}}
        self._append(market, rec)
        return rec

    def record_shadow(self, market: str, key: str, month: str, ret: float, current: float) -> None:
        rec = self.all(market)[key]
        if rec["status"] != "shadow":
            raise SkillRefused(f"{key} is {rec['status']}, not in shadow")
        if month not in rec["shadow"]:
            self._append(market, {"kind": "skill_shadow_month", "skill": key, "month": month, "ret": ret,
                                  "current": current})

    def decide(self, market: str, key: str, action: str, by: str, why: str) -> None:
        if by.strip().lower() in NOT_A_PERSON:
            raise SkillRefused("only a person may move a skill on; the agent may only propose")
        if not why.strip():
            raise SkillRefused("say why")
        skills = self.all(market)
        rec = skills.get(key)
        if rec is None:
            raise SkillRefused(f"no skill {key}")
        status = rec["status"]
        if action == "shadow":
            if status != "candidate":
                raise SkillRefused(f"{key} is {status}; only a candidate goes to shadow")
            if not rec["replays"] or rec["replays"][-1]["verdict"] != "pass":
                raise SkillRefused(f"{key} needs a passing replay first")
            new = "shadow"
        elif action == "approve":
            if status != "shadow":
                raise SkillRefused(f"{key} is {status}; only a skill in shadow can be approved")
            if len(rec["shadow"]) < SHADOW_MONTHS:
                raise SkillRefused(f"{key} needs {SHADOW_MONTHS} shadow months, has {len(rec['shadow'])}")
            new = "approved"
        elif action == "reject":
            if status not in ("candidate", "shadow"):
                raise SkillRefused(f"{key} is {status}; only a candidate or shadow skill can be rejected")
            new = "rejected"
        elif action == "retire":
            if status != "approved":
                raise SkillRefused(f"{key} is {status}; only an approved skill can be retired")
            new = "retired"
        else:
            raise SkillRefused(f"unknown action {action!r}")
        if new == "approved":
            for other, v in skills.items():
                if other != key and v["name"] == rec["name"] and v["status"] == "approved":
                    self._append(market, {"kind": "skill_status", "skill": other, "status": "retired", "by": by,
                                          "why": f"superseded by {key}"})
        self._append(market, {"kind": "skill_status", "skill": key, "status": new, "by": by, "why": why})


def show(skills: Skills, market: str) -> str:
    lines = [f"# {market.upper()} skills", "", "| skill | status | settings | last replay | shadow months | reason |",
             "|---|---|---|---|---|---|"]
    for k, v in sorted(skills.all(market).items()):
        r = v["replays"][-1] if v["replays"] else None
        rp = (f"{r['verdict']}: {r['candidate']['cagr']:+.1%} vs {r['current']['cagr']:+.1%} a year, "
              f"{r['years_won']}/{r['years']} years" if r else "")
        p = v["params"]
        lines.append(f"| {k} | {v['status']} | {p['lookback']}-{p['skip']}, top {p['n']}, band {p['band']:g}"
                     f"{', trend gate' if p['trend_gate'] else ''} | {rp} | {len(v['shadow'])} | {v['reason']} |")
    return "\n".join(lines) + "\n"
