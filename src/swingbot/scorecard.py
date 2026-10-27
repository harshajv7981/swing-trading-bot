"""swingbot-scorecard: how often each persona, the judge and the final call were right.

Every persona's final-round stance is scored as its own call against the same
price move the backtest measured: a stance above the neutral band is a long
call, below it a short call, inside it no call. The judge is scored the same
way, and "Final call" is the signal the backtest itself scored.

Per row: calls, hit rate with a coin-flip p-value, a q-value that corrects the
p-values for testing every row at once (Benjamini-Hochberg), average return per
call after costs with a 95% bootstrap range, the Brier score of conviction, and
the rank IC of stance against the move. `--by month` splits rows by the month
each trade was entered, to show whether a persona's edge holds up over time.

The leaderboard idea comes from HKUDS/AI-Trader (README says MIT;
github.com/HKUDS/AI-Trader), which ranks agents by their scored signals. Here
the agents are our personas, and the scoring is the backtest's own.

    swingbot-scorecard --run runs/<run>/results.csv runs/views.jsonl
    swingbot-scorecard --run runs/a/results.csv runs/views-a.jsonl --run runs/b/results.csv runs/views-b.jsonl --by month

Nothing here changes persona weights; it only reports.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

from .metrics import _binom_p_value, benjamini_hochberg, bootstrap_ci
from .swarm.persona_swarm import ReportAgent
from .swarm.recorded import RecordedSwarm

FINAL = "Final call"
JUDGE = "Judge"
Q_LEVEL = 0.10


def calls(results: pd.DataFrame, views_path: str | Path, run: str = "", cost_bps: float = 25.0,
          neutral_band: float | None = None) -> pd.DataFrame:
    """One row per (event, caller): the stance, the call and how it did."""
    band = ReportAgent().neutral_band if neutral_band is None else neutral_band
    swarm = RecordedSwarm(views_path)
    ok = results[results["status"] == "ok"]
    rows = []
    for _, r in ok.iterrows():
        eid, ret = str(r["event_id"]), float(r["ret"])
        base = {"run": run, "event_id": eid, "ticker": r["ticker"], "entry_date": r["entry_date"], "ret": ret}
        speakers = [(v.persona, v.stance, v.conviction) for v in swarm.views.get(eid, [])]
        debate = swarm.debates.get(eid)
        if debate is not None and debate.judge is not None:
            speakers.append((JUDGE, debate.judge.stance, debate.judge.conviction))
        speakers.append((FINAL, float(r["score"]), float(r["confidence"])))
        for who, stance, conviction in speakers:
            if who == FINAL:
                direction = {"bullish": 1, "bearish": -1}.get(str(r["sentiment"]), 0)
            else:
                direction = 1 if stance > band else -1 if stance < -band else 0
            rows.append({**base, "who": who, "stance": stance, "conviction": conviction, "direction": direction,
                         "hit": None if direction == 0 else bool(direction * ret > 0),
                         "trade_ret": None if direction == 0 else direction * ret - cost_bps / 1e4})
    return pd.DataFrame(rows)


def _row(who: str, g: pd.DataFrame) -> dict:
    made = g[g["direction"] != 0]
    n, hits = len(made), int(made["hit"].astype(bool).sum()) if len(made) else 0
    ci = bootstrap_ci(made["trade_ret"]) if n else None
    ic = None
    if len(g) >= 5 and g["stance"].nunique() > 1 and g["ret"].nunique() > 1:
        ic = float(g["stance"].rank().corr(g["ret"].rank()))
    return {
        "who": who,
        "events": int(len(g)),
        "calls": n,
        "no_call": int(len(g) - n),
        "hit_rate": hits / n if n else None,
        "p_value": _binom_p_value(hits, n) if n else None,
        "avg_trade_ret": float(made["trade_ret"].mean()) if n else None,
        "ret_ci_low": ci[0] if ci else None,
        "ret_ci_high": ci[1] if ci else None,
        "brier": float(((made["conviction"] - made["hit"].astype(float)) ** 2).mean()) if n else None,
        "ic_spearman": ic,
    }


def scorecard(table: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    if table.empty:
        return pd.DataFrame()
    table = table.copy()
    keys = ["who"]
    if by == "month":
        table["month"] = pd.to_datetime(table["entry_date"]).dt.strftime("%Y-%m")
        keys = ["month", "who"]
    rows = []
    for key, g in table.groupby(keys, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(keys, key)), **_row(key[-1], g)})
    out = pd.DataFrame(rows)
    out["q_value"] = benjamini_hochberg(list(out["p_value"]))
    order = {FINAL: 0, JUDGE: 1}
    out["_o"] = out["who"].map(lambda w: order.get(w, 2))
    out = out.sort_values([*keys[:-1], "_o", "hit_rate"], ascending=[*([True] * (len(keys) - 1)), True, False])
    return out.drop(columns="_o").reset_index(drop=True)


def format_scorecard(card: pd.DataFrame) -> str:
    def pct(x, signed=False):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "n/a"
        return f"{x * 100:+.2f}%" if signed else f"{x * 100:.0f}%"

    def num(x, nd=3):
        return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"

    lines = []
    month = None
    for _, r in card.iterrows():
        if "month" in card and r["month"] != month:
            month = r["month"]
            lines.append(f"\n{month}")
        ci = ("" if r["ret_ci_low"] is None or pd.isna(r["ret_ci_low"])
              else f" [{pct(r['ret_ci_low'], True)} to {pct(r['ret_ci_high'], True)}]")
        flag = "  <- beats luck" if r["q_value"] is not None and not pd.isna(r["q_value"]) \
            and r["q_value"] < Q_LEVEL else ""
        lines.append(
            f"{r['who']:<22} calls {r['calls']:>3} (no call {r['no_call']:>3})  hit {pct(r['hit_rate']):>4}"
            f"  p {num(r['p_value'])}  q {num(r['q_value'])}  avg {pct(r['avg_trade_ret'], True)}{ci}"
            f"  Brier {num(r['brier'])}  IC {num(r['ic_spearman'], 2)}{flag}")
    lines.append(f"\nq is the p-value corrected for testing {len(card)} rows at once; only q < {Q_LEVEL} "
                 "is marked. Few calls prove nothing either way.")
    return "\n".join(lines).lstrip("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="swingbot-scorecard", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", nargs=2, action="append", required=True, metavar=("RESULTS_CSV", "VIEWS_JSONL"),
                   help="a backtest's results.csv and the views file it scored; repeat for more runs")
    p.add_argument("--by", choices=["month"], help="split rows by entry month")
    p.add_argument("--cost-bps", type=float, default=25.0, help="round-trip cost per call, as in the backtest")
    p.add_argument("--out", help="write the scorecard CSV here")
    args = p.parse_args(argv)

    tables = []
    for results, views in args.run:
        tables.append(calls(pd.read_csv(results), views, run=str(Path(results).parent.name),
                            cost_bps=args.cost_bps))
    table = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
    card = scorecard(table, args.by)
    if card.empty:
        print("no scored events with views; nothing to report")
        return 1
    print(format_scorecard(card))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        card.to_csv(args.out, index=False)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
