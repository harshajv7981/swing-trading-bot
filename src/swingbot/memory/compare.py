"""Did the memory help? Score two runs of the same events, one without lessons
in the briefs and one with, on exactly the events both runs scored."""

from __future__ import annotations

import math

import pandas as pd

from ..metrics import _binom_p_value, summarise

KEYS = ("directional_calls", "hit_rate", "hit_rate_p_value", "trades", "avg_trade_ret", "total_trade_ret", "brier", "ic_spearman")


def _scored(results: pd.DataFrame) -> pd.DataFrame:
    return results[results["status"] == "ok"].set_index("event_id")


def compare(without: pd.DataFrame, with_: pd.DataFrame, only: set[str] | None = None) -> dict:
    a, b = _scored(without), _scored(with_)
    common = a.index.intersection(b.index)
    if only is not None:
        common = common.intersection(pd.Index(sorted(only)))
    a, b = a.loc[common], b.loc[common]

    diff = (b["trade_ret"] - a["trade_ret"]).astype(float)
    changed = diff[diff.abs() > 1e-12]
    better = int((changed > 0).sum())
    out = {
        "events": int(len(common)),
        "without": {k: summarise(a.reset_index()).get(k) for k in KEYS},
        "with": {k: summarise(b.reset_index()).get(k) for k in KEYS},
        "calls_changed": int((a["sentiment"] != b["sentiment"]).sum()),
        "events_where_return_differs": int(len(changed)),
        "memory_better": better,
        "memory_worse": int(len(changed) - better),
        # One-sided sign test: could memory's wins among the changed events be luck?
        "sign_test_p_value": _binom_p_value(better, len(changed)) if len(changed) else None,
        "avg_paired_diff": float(diff.mean()) if len(diff) else None,
    }
    return out


def format_compare(c: dict) -> str:
    def f(k, v):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return "n/a"
        if k in ("hit_rate", "avg_trade_ret", "total_trade_ret"):
            return f"{v * 100:+.2f}%" if k != "hit_rate" else f"{v * 100:.1f}%"
        return f"{v:.3f}" if isinstance(v, float) else str(v)

    lines = [f"Events scored by both runs: {c['events']}", "", f"{'':22s}{'without':>12s}{'with':>12s}"]
    for k in KEYS:
        lines.append(f"{k:22s}{f(k, c['without'][k]):>12s}{f(k, c['with'][k]):>12s}")
    lines += [
        "",
        f"Calls that changed: {c['calls_changed']}",
        f"Events where the return differs: {c['events_where_return_differs']} "
        f"(memory better {c['memory_better']}, worse {c['memory_worse']}, "
        f"sign test p={f('p', c['sign_test_p_value'])})",
        f"Average paired difference per event: {f('avg_trade_ret', c['avg_paired_diff'])}",
    ]
    return "\n".join(lines)
