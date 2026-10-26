"""Summary statistics for a backtest result frame."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _binom_p_value(hits: int, n: int) -> float:
    """One-sided P(X >= hits) under a fair coin. Tells you if the hit rate beats luck."""
    if n == 0:
        return float("nan")
    return float(sum(math.comb(n, k) for k in range(hits, n + 1)) / 2**n)


def summarise(results: pd.DataFrame) -> dict:
    ok = results[results.get("status") == "ok"] if "status" in results else results.iloc[0:0]
    calls = ok[ok["sentiment"] != "neutral"] if len(ok) else ok
    n_calls = len(calls)
    hits = int(calls["hit"].astype(bool).sum()) if n_calls else 0

    summary: dict = {
        "events": int(len(results)),
        "scored": int(len(ok)),
        "skipped": int(len(results) - len(ok)),
        "directional_calls": n_calls,
        "neutral": int(len(ok) - n_calls),
        "hit_rate": hits / n_calls if n_calls else None,
        "hit_rate_p_value": _binom_p_value(hits, n_calls) if n_calls else None,
        "avg_trade_ret": float(calls["trade_ret"].mean()) if n_calls else None,
        "total_trade_ret": float(calls["trade_ret"].sum()) if n_calls else None,
    }

    if n_calls:
        w = calls["confidence"].astype(float)
        summary["conf_weighted_trade_ret"] = float((w * calls["trade_ret"]).sum() / w.sum())
        # Brier score: confidence read as P(call is right). Lower is better; 0.25 = coin flip.
        summary["brier"] = float(((w - calls["hit"].astype(float)) ** 2).mean())

        by_side = {}
        for side, g in calls.groupby("sentiment"):
            by_side[side] = {
                "n": int(len(g)),
                "hit_rate": float(g["hit"].astype(bool).mean()),
                "avg_trade_ret": float(g["trade_ret"].mean()),
            }
        summary["by_sentiment"] = by_side

        buckets = pd.cut(w, [0, 0.5, 0.7, 1.0], include_lowest=True, labels=["<=0.50", "0.50-0.70", ">0.70"])
        by_conf = {}
        for b, g in calls.groupby(buckets, observed=True):
            by_conf[str(b)] = {"n": int(len(g)), "hit_rate": float(g["hit"].astype(bool).mean())}
        summary["by_confidence"] = by_conf

    if len(ok) >= 3:
        # Information coefficient: rank correlation of crowd score with next move.
        summary["ic_spearman"] = float(ok["score"].rank().corr(ok["ret"].rank()))
        # Baseline: just buying every name on every news item.
        summary["baseline_always_long_avg_ret"] = float(ok["ret"].mean())
        if "excess_ret" in ok and ok["excess_ret"].notna().any():
            signed = np.sign(calls["score"]) * calls["excess_ret"] if n_calls else pd.Series(dtype=float)
            summary["avg_excess_trade_ret_vs_benchmark"] = float(signed.mean()) if n_calls else None

    return summary


def format_summary(s: dict) -> str:
    def pct(x):
        return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.2f}%"

    lines = [
        f"Events: {s['events']}  scored: {s['scored']}  skipped: {s['skipped']}",
        f"Directional calls: {s['directional_calls']}  neutral: {s['neutral']}",
    ]
    if s["directional_calls"]:
        lines += [
            f"Hit rate: {s['hit_rate'] * 100:.1f}%  (p={s['hit_rate_p_value']:.3f} vs coin flip)",
            f"Avg trade return after costs: {pct(s['avg_trade_ret'])}",
            f"Confidence-weighted trade return: {pct(s['conf_weighted_trade_ret'])}",
            f"Brier score: {s['brier']:.3f}  (0.25 = coin flip, lower is better)",
        ]
        for side, d in s.get("by_sentiment", {}).items():
            lines.append(f"  {side:8s} n={d['n']:<3d} hit {d['hit_rate'] * 100:5.1f}%  avg {pct(d['avg_trade_ret'])}")
        for b, d in s.get("by_confidence", {}).items():
            lines.append(f"  conf {b:9s} n={d['n']:<3d} hit {d['hit_rate'] * 100:5.1f}%")
    if "ic_spearman" in s:
        lines.append(f"Rank IC (score vs move): {s['ic_spearman']:+.3f}")
        lines.append(f"Baseline, always long: {pct(s['baseline_always_long_avg_ret'])}")
    if s.get("avg_excess_trade_ret_vs_benchmark") is not None:
        lines.append(f"Avg directional return vs benchmark: {pct(s['avg_excess_trade_ret_vs_benchmark'])}")
    return "\n".join(lines)
