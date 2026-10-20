"""Performance scoring and learned topic prioritisation.

perf (0-100) = 60% reach outlier + 40% engagement quality
  reach_mult = reach / median reach of the account's measured posts (capped at 5x)
  eng_rate   = (3*shares + 3*saves + 2*comments + likes) / reach   (capped at 15%)
Each dimension value (pillar, hook formula, publishing hour, source domain) gets a multiplier from its
shrunk mean performance: multiplier = clamp(sqrt(shrunk_mean / global_mean), min, max). Pillar and hook
formula multipliers are used when scoring stories and picking hook formulas.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Any

from ..settings import Settings
from ..store import Store

log = logging.getLogger(__name__)


def perf_score(row: dict[str, Any], median_reach: float) -> float:
    reach = float(row.get("reach") or 0.0)
    if reach <= 0:
        return 0.0
    reach_mult = min(reach / median_reach, 5.0) / 5.0 if median_reach > 0 else 0.2
    engagement = (3 * float(row.get("shares") or 0) + 3 * float(row.get("saved") or 0)
                  + 2 * float(row.get("comments") or 0) + float(row.get("likes") or 0))
    eng_rate = min(engagement / reach, 0.15) / 0.15
    return round(100.0 * (0.6 * reach_mult + 0.4 * eng_rate), 2)


def recompute_perf(store: Store) -> int:
    rows = store.latest_metrics()
    reaches = [float(r["reach"]) for r in rows if r.get("reach")]
    median = statistics.median(reaches) if reaches else 0.0
    for row in rows:
        store.set_perf(row["post_id"], row["checkpoint_h"], perf_score(row, median))
    return len(rows)


def update_weights(store: Store, settings: Settings) -> dict[str, dict[str, float]]:
    cfg = settings.get("optimize", {}) or {}
    k = float(cfg.get("shrink_k", 4.0))
    lo, hi = float(cfg.get("min_mult", 0.7)), float(cfg.get("max_mult", 1.4))
    recompute_perf(store)
    rows = [r for r in store.latest_metrics() if r.get("perf") is not None]
    if len(rows) < int(cfg.get("min_posts", 5)):
        log.info("optimizer: %d measured posts (need %s); keeping priors", len(rows), cfg.get("min_posts", 5))
        return {}
    global_mean = statistics.mean(float(r["perf"]) for r in rows)
    result: dict[str, dict[str, float]] = {}
    dims = {"pillar": lambda r: r.get("pillar"), "hook_formula": lambda r: r.get("hook_formula"),
            "hour_ist": lambda r: None if r.get("hour_ist") is None else f"h{int(r['hour_ist']):02d}",
            "source_domain": lambda r: r.get("source_domain")}
    for dim, getter in dims.items():
        groups: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            key = getter(row)
            if key is not None:
                groups[str(key)].append(float(row["perf"]))
        result[dim] = {}
        for key, values in groups.items():
            n, mean = len(values), statistics.mean(values)
            shrunk = (n * mean + k * global_mean) / (n + k)
            multiplier = 1.0 if global_mean <= 0 else max(lo, min(hi, (shrunk / global_mean) ** 0.5))
            store.set_weight(dim, key, n, mean, multiplier)
            result[dim][key] = round(multiplier, 3)
    log.info("optimizer updated weights from %d posts (global mean perf %.1f)", len(rows), global_mean)
    return result


def report(store: Store) -> str:
    rows = store.all_weights()
    if not rows:
        return "No learned weights yet (need measured posts)."
    lines = ["dim            key                 n    mean   multiplier"]
    for r in rows:
        lines.append(f"{r['dim']:<14} {str(r['key']):<19} {r['n']:<4} {r['mean']:<6.1f} {r['multiplier']:.3f}")
    return "\n".join(lines)
