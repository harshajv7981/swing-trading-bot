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


def _traded(ok: pd.DataFrame) -> pd.DataFrame:
    if "traded" not in ok or not len(ok):
        return ok.iloc[0:0]
    return ok[ok["traded"].astype(str).str.lower() == "true"]


def summarise(results: pd.DataFrame) -> dict:
    """Two scores, kept apart on purpose.

    Directional accuracy grades the swarm: every bullish and bearish call
    against the move to the horizon close. The trade stats are what the
    long-only bot would have made: bullish calls only, with the planned stop
    and target, after costs. A bearish call can be right and still earn nothing.
    """
    ok = results[results.get("status") == "ok"] if "status" in results else results.iloc[0:0]
    calls = ok[ok["sentiment"] != "neutral"] if len(ok) else ok
    n_calls = len(calls)
    hits = int(calls["hit"].astype(bool).sum()) if n_calls else 0
    trades = _traded(ok)
    n_trades = len(trades)

    summary: dict = {
        "events": int(len(results)),
        "scored": int(len(ok)),
        "skipped": int(len(results) - len(ok)),
        "directional_calls": n_calls,
        "neutral": int(len(ok) - n_calls),
        "hit_rate": hits / n_calls if n_calls else None,
        "hit_rate_p_value": _binom_p_value(hits, n_calls) if n_calls else None,
        "trades": n_trades,
        "win_rate": float((trades["trade_ret"] > 0).mean()) if n_trades else None,
        "avg_trade_ret": float(trades["trade_ret"].mean()) if n_trades else None,
        "total_trade_ret": float(trades["trade_ret"].sum()) if n_trades else None,
    }

    if n_calls:
        w = calls["confidence"].astype(float)
        # Brier score: confidence read as P(call is right). Lower is better; 0.25 = coin flip.
        summary["brier"] = float(((w - calls["hit"].astype(float)) ** 2).mean())

        by_side = {}
        for side, g in calls.groupby("sentiment"):
            by_side[side] = {"n": int(len(g)), "hit_rate": float(g["hit"].astype(bool).mean())}
        summary["by_sentiment"] = by_side

        buckets = pd.cut(w, [0, 0.5, 0.7, 1.0], include_lowest=True, labels=["<=0.50", "0.50-0.70", ">0.70"])
        by_conf = {}
        for b, g in calls.groupby(buckets, observed=True):
            by_conf[str(b)] = {"n": int(len(g)), "hit_rate": float(g["hit"].astype(bool).mean())}
        summary["by_confidence"] = by_conf

    if n_trades:
        w = trades["confidence"].astype(float)
        summary["conf_weighted_trade_ret"] = float((w * trades["trade_ret"]).sum() / w.sum())
        if "trade_r" in trades:
            summary["avg_r"] = float(trades["trade_r"].astype(float).mean())
        if "exit_reason" in trades:
            summary["exit_reasons"] = {str(k): int(v) for k, v in trades["exit_reason"].value_counts().items()}
    if "no_trade_reason" in calls and n_calls:
        untaken = calls[calls["no_trade_reason"].notna()]["no_trade_reason"].astype(str)
        if len(untaken):
            kinds = untaken.str.split(":").str[0]
            summary["calls_not_traded"] = {str(k): int(v) for k, v in kinds.value_counts().items()}

    if len(ok) >= 3:
        # Information coefficient: rank correlation of crowd score with next move.
        summary["ic_spearman"] = float(ok["score"].rank().corr(ok["ret"].rank()))
        # Baseline: just buying every name on every news item and holding to the horizon.
        summary["baseline_always_long_avg_ret"] = float(ok["ret"].mean())
        if "excess_ret" in ok and ok["excess_ret"].notna().any():
            # Directional (bearish calls flip the sign): an accuracy measure, not money made.
            signed = np.sign(calls["score"]) * calls["excess_ret"] if n_calls else pd.Series(dtype=float)
            summary["avg_excess_trade_ret_vs_benchmark"] = float(signed.mean()) if n_calls else None

    return summary


def format_summary(s: dict) -> str:
    def pct(x):
        return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.2f}%"

    lines = [
        f"Events: {s['events']}  scored: {s['scored']}  skipped: {s['skipped']}",
        "",
        "Directional accuracy (every bullish and bearish call, to the horizon close):",
        f"  Directional calls: {s['directional_calls']}  neutral: {s['neutral']}",
    ]
    if s["directional_calls"]:
        lines += [
            f"  Hit rate: {s['hit_rate'] * 100:.1f}%  (p={s['hit_rate_p_value']:.3f} vs coin flip)",
            f"  Brier score: {s['brier']:.3f}  (0.25 = coin flip, lower is better)",
        ]
        for side, d in s.get("by_sentiment", {}).items():
            lines.append(f"    {side:8s} n={d['n']:<3d} hit {d['hit_rate'] * 100:5.1f}%")
        for b, d in s.get("by_confidence", {}).items():
            lines.append(f"    conf {b:9s} n={d['n']:<3d} hit {d['hit_rate'] * 100:5.1f}%")
    if "ic_spearman" in s:
        lines.append(f"  Rank IC (score vs move): {s['ic_spearman']:+.3f}")
    if s.get("avg_excess_trade_ret_vs_benchmark") is not None:
        lines.append(f"  Avg directional move vs benchmark: {pct(s['avg_excess_trade_ret_vs_benchmark'])}")

    lines += ["", "Long-only trades (bullish calls, planned stop and target, after costs):",
              f"  Trades: {s.get('trades', 0)}"]
    not_traded = s.get("calls_not_traded")
    if not_traded:
        lines[-1] += "  calls not traded: " + ", ".join(f"{k} {v}" for k, v in not_traded.items())
    if s.get("trades"):
        lines += [
            f"  Win rate: {s['win_rate'] * 100:.1f}%",
            f"  Avg trade return: {pct(s['avg_trade_ret'])}  total {pct(s['total_trade_ret'])}",
            f"  Confidence-weighted trade return: {pct(s['conf_weighted_trade_ret'])}",
        ]
        if s.get("avg_r") is not None:
            lines.append(f"  Avg R multiple: {s['avg_r']:+.2f}R")
        if s.get("exit_reasons"):
            lines.append("  Exits: " + ", ".join(f"{k} {v}" for k, v in s["exit_reasons"].items()))
    if "baseline_always_long_avg_ret" in s:
        lines.append(f"  Baseline, always long to the horizon: {pct(s['baseline_always_long_avg_ret'])}")
    return "\n".join(lines)


def benjamini_hochberg(p_values: list[float | None]) -> list[float | None]:
    """Benjamini-Hochberg q-values, in the input order; None stays None.

    When many hit rates are tested at once (every persona, every month), a few
    will beat p < 0.05 by luck alone. A q-value is the p-value adjusted for how
    many were tested: q < 0.10 means at most about 1 in 10 such findings is a
    fluke. The idea is borrowed from HKUDS/AI-Trader's research scripts.
    """
    present = sorted((p, i) for i, p in enumerate(p_values) if p is not None and not math.isnan(p))
    m = len(present)
    q: list[float | None] = [None] * len(p_values)
    running = 1.0
    for rank in range(m, 0, -1):
        p, i = present[rank - 1]
        running = min(running, p * m / rank)
        q[i] = min(running, 1.0)
    return q


def bootstrap_ci(values, n: int = 2000, seed: int = 19, level: float = 0.95) -> tuple[float, float] | None:
    """Percentile bootstrap interval for the mean; None with fewer than 2 values."""
    v = np.asarray(list(values), dtype=float)
    if len(v) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), size=(n, len(v)))].mean(axis=1)
    lo, hi = np.quantile(means, [(1 - level) / 2, 1 - (1 - level) / 2])
    return float(lo), float(hi)
