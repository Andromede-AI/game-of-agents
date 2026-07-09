"""Chip-based performance metrics — alternative to TrueSkill.

TrueSkill measures placement consistency (who survives freezeout matches),
not actual poker winnings. This module computes per-agent chip profit —
total chips won/lost across all games — as a complementary metric.

Usage:
    uv run python -m analysis.chip_metrics .goa_data/runs/run_XXX.json
    uv run python -m analysis.chip_metrics --all       # all exported runs
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from analysis.loader import RunData, load_run


STARTING_CHIPS = 200  # from game config


def compute_chip_profit(run: RunData) -> dict[str, float]:
    """Per-agent total chip profit across all finished games."""
    profit: dict[str, float] = defaultdict(float)
    for g in run.finished_games:
        for p in g.participants:
            aid = p.get("agent_id", "")
            ending = p.get("ending_chips", 0) or 0
            profit[aid] += ending - STARTING_CHIPS
    return dict(profit)


def compute_chip_rank(run: RunData) -> list[tuple[str, float, int]]:
    """Return agents sorted by chip profit (highest first)."""
    profit = compute_chip_profit(run)
    ranked = sorted(profit.items(), key=lambda kv: -kv[1])
    return [(aid, chips, i + 1) for i, (aid, chips) in enumerate(ranked)]


def kendall_tau(rank_a: list[str], rank_b: list[str]) -> float:
    """Kendall tau between two rankings of the same agents."""
    from itertools import combinations

    agents = rank_a
    b_pos = {a: i for i, a in enumerate(rank_b)}
    concordant = discordant = 0
    for i, j in combinations(range(len(agents)), 2):
        a_order = True  # i before j in rank_a
        b_order = b_pos.get(agents[i], 0) < b_pos.get(agents[j], 0)
        if a_order == b_order:
            concordant += 1
        else:
            discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 0.0


def compare_metrics(run: RunData) -> dict:
    """Compare TrueSkill rank vs chip-profit rank for one run."""
    ts_rank = [a.agent_id for a in sorted(run.agents, key=lambda a: -a.best_elo)]
    chip_profit = compute_chip_profit(run)
    chip_rank = sorted(chip_profit.keys(), key=lambda a: -chip_profit[a])

    tau = kendall_tau(ts_rank, chip_rank)

    return {
        "run_id": run.run_id,
        "n_agents": len(run.agents),
        "n_games": len(run.finished_games),
        "tau": tau,
        "trueskill_rank": ts_rank,
        "chip_rank": chip_rank,
        "chip_profit": chip_profit,
        "trueskill_top": ts_rank[0] if ts_rank else None,
        "chip_top": chip_rank[0] if chip_rank else None,
        "agree_on_top": (ts_rank[0] == chip_rank[0]) if ts_rank and chip_rank else None,
    }


def print_comparison(run: RunData) -> None:
    result = compare_metrics(run)
    name = (run.config or {}).get("name", run.run_id)
    print(f"\n=== {name} ({run.run_id}) ===")
    print(f"  Games: {result['n_games']}  Agents: {result['n_agents']}")
    print(f"  Kendall tau (TrueSkill vs Chips): {result['tau']:.3f}")
    print(f"  TrueSkill #1: {result['trueskill_top']}  Chip #1: {result['chip_top']}  Agree: {result['agree_on_top']}")
    print()
    print(f"  {'Agent':<12} {'TS_ELO':>10} {'TS_Rank':>8} {'Chip_Profit':>12} {'Chip_Rank':>10}")
    ts_agents = {a.agent_id: a for a in run.agents}
    for i, aid in enumerate(result["trueskill_rank"]):
        a = ts_agents.get(aid)
        elo = a.best_elo if a else 0
        chip_r = result["chip_rank"].index(aid) + 1 if aid in result["chip_rank"] else "?"
        chips = result["chip_profit"].get(aid, 0)
        print(f"  {aid:<12} {elo:>10.2f} {i+1:>8} {chips:>12.0f} {chip_r:>10}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "--all":
        runs_dir = Path(".goa_data/runs")
        for p in sorted(runs_dir.glob("*.json")):
            try:
                r = load_run(p)
                if r.finished_games:
                    print_comparison(r)
            except Exception as e:
                print(f"  Error loading {p.name}: {e}")
    else:
        r = load_run(sys.argv[1])
        print_comparison(r)
