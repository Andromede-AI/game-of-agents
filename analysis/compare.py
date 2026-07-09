"""Compare metrics across multiple runs (conditions/replications)."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from analysis.loader import RunData, load_run
from analysis.metrics import compute_agent_stats, compute_marketplace_stats, compute_pairwise_stats


@dataclass
class ConditionSummary:
    """Aggregated metrics for one experimental condition across replications."""
    condition: str
    runs: list[RunData]
    n_runs: int = 0
    # Aggregated metrics (lists across replications for bootstrap)
    avg_win_rates: list[float] = field(default_factory=list)
    avg_aggressions: list[float] = field(default_factory=list)
    avg_bots_submitted: list[float] = field(default_factory=list)
    marketplace_offers_per_run: list[int] = field(default_factory=list)
    marketplace_purchases_per_run: list[int] = field(default_factory=list)
    chat_messages_per_run: list[int] = field(default_factory=list)
    games_per_run: list[int] = field(default_factory=list)
    # Coordination metrics (only for heterogeneous conditions)
    same_model_aggression: list[float] = field(default_factory=list)
    cross_model_aggression: list[float] = field(default_factory=list)
    same_model_purchase_frac: list[float] = field(default_factory=list)


def summarize_condition(condition: str, runs: list[RunData]) -> ConditionSummary:
    """Compute aggregated metrics for a condition across replications."""
    cs = ConditionSummary(condition=condition, runs=runs, n_runs=len(runs))

    for run in runs:
        stats = compute_agent_stats(run)
        agents = list(stats.values())

        cs.avg_win_rates.append(sum(a.win_rate for a in agents) / len(agents) if agents else 0)
        cs.avg_aggressions.append(sum(a.aggression_factor for a in agents) / len(agents) if agents else 0)
        cs.avg_bots_submitted.append(sum(a.bots_submitted for a in agents) / len(agents) if agents else 0)
        cs.marketplace_offers_per_run.append(len(run.offers))
        cs.marketplace_purchases_per_run.append(len(run.purchases))
        cs.chat_messages_per_run.append(len(run.comments))
        cs.games_per_run.append(len(run.finished_games))

        # Coordination metrics
        pairwise = compute_pairwise_stats(run)
        same_agg = []
        cross_agg = []
        for ps in pairwise:
            model_a = run.agent_model(ps.agent_a)
            model_b = run.agent_model(ps.agent_b)
            if model_a and model_b and ps.games_together > 0:
                if model_a == model_b:
                    same_agg.append(ps.a_aggression)
                else:
                    cross_agg.append(ps.a_aggression)

        if same_agg:
            cs.same_model_aggression.append(sum(same_agg) / len(same_agg))
        if cross_agg:
            cs.cross_model_aggression.append(sum(cross_agg) / len(cross_agg))

        ms = compute_marketplace_stats(run)
        total = ms.same_model_purchases + ms.cross_model_purchases
        if total > 0:
            cs.same_model_purchase_frac.append(ms.same_model_purchases / total)

    return cs


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _bootstrap_ci(
    xs: list[float],
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 17,
) -> tuple[float, float]:
    """Bootstrap confidence interval.

    Uses a fixed ``seed`` (default 17) so that figure regeneration is
    deterministic across runs. Prior to Apr 21 the RNG was unseeded, which
    caused the annotation on ``fig_coordination_signal.pdf`` to drift by
    ~1e-3 on each regeneration even though the underlying data were stable.
    Paper appendix §G claims byte-level regenerability; this seeding makes
    that claim hold for the bootstrap-CI-bearing figures.
    """
    if len(xs) < 2:
        return (xs[0] if xs else 0.0, xs[0] if xs else 0.0)
    import random
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        sample = [rng.choice(xs) for _ in range(len(xs))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(n_boot * alpha / 2)]
    hi = means[int(n_boot * (1 - alpha / 2))]
    return (lo, hi)


def print_comparison(summaries: list[ConditionSummary]) -> None:
    """Print a comparison table across conditions."""
    print(f"\n{'Condition':<25} {'N':>3} {'Games':>7} {'AvgAF':>7} {'Offers':>7} "
          f"{'Buys':>6} {'Chat':>6} {'SameAF':>7} {'CrossAF':>8} {'InGrp%':>7}")
    print("-" * 95)

    for cs in summaries:
        same_af = f"{_mean(cs.same_model_aggression):.3f}" if cs.same_model_aggression else "n/a"
        cross_af = f"{_mean(cs.cross_model_aggression):.3f}" if cs.cross_model_aggression else "n/a"
        ingrp = f"{_mean(cs.same_model_purchase_frac):.0%}" if cs.same_model_purchase_frac else "n/a"

        print(f"{cs.condition:<25} {cs.n_runs:>3} "
              f"{_mean(cs.games_per_run):>7.0f} "
              f"{_mean(cs.avg_aggressions):>7.3f} "
              f"{_mean(cs.marketplace_offers_per_run):>7.1f} "
              f"{_mean(cs.marketplace_purchases_per_run):>6.1f} "
              f"{_mean(cs.chat_messages_per_run):>6.1f} "
              f"{same_af:>7} "
              f"{cross_af:>8} "
              f"{ingrp:>7}")

    # Coordination delta (if heterogeneous condition exists)
    hetero = [cs for cs in summaries if cs.same_model_aggression and cs.cross_model_aggression]
    for cs in hetero:
        delta = _mean(cs.same_model_aggression) - _mean(cs.cross_model_aggression)
        print(f"\n  [{cs.condition}] Coordination signal: "
              f"same-model AF - cross-model AF = {delta:+.4f}")
        if abs(delta) > 0.05:
            direction = "LESS" if delta < 0 else "MORE"
            print(f"  → Agents are {direction} aggressive toward same-model opponents")
        else:
            print(f"  → No clear coordination signal (|delta| < 0.05)")


def load_condition(run_dir: str | Path, pattern: str) -> list[RunData]:
    """Load all runs matching a filename pattern from a directory."""
    run_dir = Path(run_dir)
    runs = []
    for path in sorted(run_dir.glob("*.json")):
        try:
            run = load_run(path)
            if pattern in run.config.get("name", "") or pattern in str(path):
                runs.append(run)
        except Exception:
            continue
    return runs


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Compare experimental conditions")
    parser.add_argument("run_dir", help="Directory containing RunState JSON files")
    parser.add_argument("--conditions", nargs="+", default=["baseline", "full", "homo-claude", "homo-gemini"],
                        help="Condition name patterns to match against run config names")
    args = parser.parse_args()

    summaries = []
    for cond in args.conditions:
        runs = load_condition(args.run_dir, cond)
        if runs:
            summaries.append(summarize_condition(cond, runs))
            print(f"Loaded {len(runs)} runs for condition '{cond}'")
        else:
            print(f"No runs found for condition '{cond}'")

    if summaries:
        print_comparison(summaries)
