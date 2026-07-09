#!/usr/bin/env python3
"""Compare TrueSkill ranking vs net chip profit ranking per agent.

For each run, computes:
  - Net chips won/lost per agent (summing chip_delta across all games)
  - TrueSkill rank (from best_elo)
  - Spearman rank correlation between the two
  - Per-agent breakdown showing where they diverge

Usage:
    uv run python scripts/chip_vs_trueskill.py                    # all exported runs
    uv run python scripts/chip_vs_trueskill.py .goa_data/runs/run_xxx.json  # specific run(s)
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from analysis.loader import RunData, load_run


def compute_chip_profits(run: RunData) -> dict[str, dict]:
    """Compute net chip profit per agent across all finished games.

    Handles two data schemas:
      - Local runs: participants have chip_delta, bb_won, starting_chips
      - Convex exports: participants have ending_chips only; starting_stack
        comes from config

    Returns dict of agent_id -> {
        total_chip_delta, total_bb_won, games_played, wins,
        avg_chip_delta, avg_bb_won
    }
    """
    starting_stack = run.config.get("game", {}).get("starting_stack", 200)
    big_blind = run.config.get("game", {}).get("big_blind", 10)

    stats: dict[str, dict] = {}
    for agent in run.agents:
        stats[agent.agent_id] = {
            "total_chip_delta": 0,
            "total_bb_won": 0.0,
            "games_played": 0,
            "wins": 0,
        }

    for game in run.finished_games:
        for p in game.participants:
            aid = p.get("agent_id")
            if aid not in stats:
                continue

            # Compute chip_delta from whichever fields are available
            if "chip_delta" in p and p["chip_delta"] is not None:
                delta = p["chip_delta"]
            elif "ending_chips" in p and p["ending_chips"] is not None:
                start = p.get("starting_chips") or starting_stack
                delta = p["ending_chips"] - start
            else:
                delta = 0

            # bb_won: use if available, otherwise derive from delta
            bb_won = p.get("bb_won")
            if bb_won is None and big_blind:
                bb_won = delta / big_blind

            stats[aid]["total_chip_delta"] += delta
            stats[aid]["total_bb_won"] += bb_won or 0
            stats[aid]["games_played"] += 1
            placement = p.get("placement")
            if placement == 1 or placement == 1.0:
                stats[aid]["wins"] += 1

    for aid, s in stats.items():
        n = s["games_played"] or 1
        s["avg_chip_delta"] = s["total_chip_delta"] / n
        s["avg_bb_won"] = s["total_bb_won"] / n

    return stats


def rank_agents(values: dict[str, float], descending: bool = True) -> dict[str, int]:
    """Rank agents by value. Rank 1 = best. Ties get average rank."""
    sorted_ids = sorted(values, key=lambda k: values[k], reverse=descending)
    ranks: dict[str, int] = {}
    i = 0
    while i < len(sorted_ids):
        # Find ties
        j = i + 1
        while j < len(sorted_ids) and values[sorted_ids[j]] == values[sorted_ids[i]]:
            j += 1
        avg_rank = (i + 1 + j) / 2  # average rank for tied group
        for k in range(i, j):
            ranks[sorted_ids[k]] = avg_rank
        i = j
    return ranks


def spearman_rho(ranks_a: dict[str, float], ranks_b: dict[str, float]) -> float:
    """Spearman rank correlation between two rankings."""
    agents = sorted(set(ranks_a) & set(ranks_b))
    n = len(agents)
    if n < 2:
        return float("nan")
    d_sq = sum((ranks_a[a] - ranks_b[a]) ** 2 for a in agents)
    return 1 - (6 * d_sq) / (n * (n * n - 1))


def analyze_run(run: RunData) -> None:
    """Print chip-profit vs TrueSkill comparison for one run."""
    chip_stats = compute_chip_profits(run)
    config = run.config
    name = config.get("name", run.run_id)
    n_games = len(run.finished_games)

    if n_games == 0:
        print(f"\n{'='*70}")
        print(f"Run: {name} ({run.run_id}) — 0 finished games, skipping")
        return

    # Build value dicts for ranking
    elo_values = {a.agent_id: a.best_elo for a in run.agents}
    chip_values = {a: s["total_chip_delta"] for a, s in chip_stats.items()}
    bb_values = {a: s["total_bb_won"] for a, s in chip_stats.items()}

    elo_ranks = rank_agents(elo_values)
    chip_ranks = rank_agents(chip_values)

    rho_chip = spearman_rho(elo_ranks, chip_ranks)

    # Header
    print(f"\n{'='*70}")
    print(f"Run: {name} ({run.run_id})")
    print(f"Games: {n_games}  |  Agents: {len(run.agents)}  |  "
          f"Spearman ρ (ELO vs chips): {rho_chip:+.3f}")
    print(f"{'='*70}")

    # Table
    agents_sorted = sorted(run.agents, key=lambda a: a.best_elo, reverse=True)
    print(f"{'Agent':<14} {'ELO':>8} {'ELO Rk':>7} {'Chips':>10} {'Chip Rk':>8} "
          f"{'Δ Rank':>7} {'BB Won':>10} {'Games':>6} {'Wins':>5} {'Avg Chip':>9}")
    print("-" * 100)

    for agent in agents_sorted:
        aid = agent.agent_id
        s = chip_stats[aid]
        elo_rk = elo_ranks[aid]
        chip_rk = chip_ranks[aid]
        rank_diff = elo_rk - chip_rk  # positive = ELO ranks them worse than chips do

        marker = ""
        if abs(rank_diff) >= 2:
            marker = " <<"  # flag large divergence

        print(f"{aid:<14} {agent.best_elo:>8.1f} {elo_rk:>7.1f} "
              f"{s['total_chip_delta']:>+10.0f} {chip_rk:>8.1f} "
              f"{rank_diff:>+7.1f}{marker} "
              f"{s['total_bb_won']:>+10.1f} {s['games_played']:>6d} "
              f"{s['wins']:>5d} {s['avg_chip_delta']:>+9.1f}")

    # Interpretation
    print()
    if abs(rho_chip) >= 0.9:
        print(f"  → Strong agreement (ρ={rho_chip:+.3f}): TrueSkill tracks chip profit well.")
    elif abs(rho_chip) >= 0.6:
        print(f"  → Moderate agreement (ρ={rho_chip:+.3f}): some divergence between TrueSkill and chips.")
    else:
        print(f"  → Weak agreement (ρ={rho_chip:+.3f}): TrueSkill and chip profit tell different stories.")

    # Flag biggest divergences
    divergences = [(aid, elo_ranks[aid] - chip_ranks[aid]) for aid in chip_stats]
    divergences.sort(key=lambda x: abs(x[1]), reverse=True)
    big = [(aid, d) for aid, d in divergences if abs(d) >= 2]
    if big:
        print(f"  → Biggest rank divergences:")
        for aid, d in big:
            direction = "chips rank higher" if d > 0 else "ELO ranks higher"
            print(f"    {aid}: {abs(d):.1f} rank gap ({direction})")


def main():
    runs_dir = Path(".goa_data/runs")

    if len(sys.argv) > 1:
        paths = [Path(p) for p in sys.argv[1:]]
    else:
        if not runs_dir.exists():
            print(f"No runs directory found at {runs_dir}")
            sys.exit(1)
        paths = sorted(runs_dir.glob("*.json"))

    if not paths:
        print("No run files found.")
        sys.exit(1)

    # Aggregate stats across all runs
    all_rhos = []

    for path in paths:
        try:
            run = load_run(path)
        except Exception as e:
            print(f"\nSkipping {path.name}: {e}")
            continue

        if len(run.finished_games) == 0:
            continue

        chip_stats = compute_chip_profits(run)
        elo_values = {a.agent_id: a.best_elo for a in run.agents}
        chip_values = {a: s["total_chip_delta"] for a, s in chip_stats.items()}
        elo_ranks = rank_agents(elo_values)
        chip_ranks = rank_agents(chip_values)
        rho = spearman_rho(elo_ranks, chip_ranks)

        analyze_run(run)
        if rho == rho:  # not NaN
            all_rhos.append((run.config.get("name", run.run_id), rho))

    if len(all_rhos) > 1:
        print(f"\n{'='*70}")
        print("SUMMARY ACROSS ALL RUNS")
        print(f"{'='*70}")
        print(f"{'Run':<50} {'ρ':>8}")
        print("-" * 60)
        for name, rho in sorted(all_rhos, key=lambda x: x[1]):
            print(f"{name:<50} {rho:>+8.3f}")
        mean_rho = sum(r for _, r in all_rhos) / len(all_rhos)
        print(f"\nMean ρ across {len(all_rhos)} runs: {mean_rho:+.3f}")


if __name__ == "__main__":
    main()
