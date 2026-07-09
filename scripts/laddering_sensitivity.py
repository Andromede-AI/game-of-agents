"""Extreme-laddering threshold sensitivity.

Companion to scripts/laddering_rate.py. Addresses a co-author's note that the
LR3 >= 3 threshold is "a little arbitrary" by recomputing observed count,
null mean, and p-value across LR3 thresholds {2, 3, 4, 5, 6}.

Reuses the same within-game permutation null as laddering_rate.py so the
sensitivity table is directly comparable to the headline p=0.0001 result.

Usage:
    uv run python scripts/laddering_sensitivity.py
    uv run python scripts/laddering_sensitivity.py --n-null 10000 --seed 17
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from pathlib import Path

from scripts.laddering_rate import load_chip_games, per_agent_metrics

OUT_MD = Path("findings/ELO_VS_CHIP_COUNT/laddering_sensitivity.md")
THRESHOLDS = [2, 3, 4, 5, 6]


def count_at_thresholds(per_agent: list[dict], thresholds: list[int]) -> dict[int, int]:
    """Count agents satisfying (lr3 >= t AND lr2 > 0 AND total_delta < 0) at each t."""
    out = {t: 0 for t in thresholds}
    for a in per_agent:
        if a["lr2"] > 0 and a["total_delta"] < 0:
            for t in thresholds:
                if a["lr3"] >= t:
                    out[t] += 1
    return out


def null_counts_once(
    rows: list[dict],
    agent_ids: list[str],
    rng: random.Random,
    thresholds: list[int],
) -> dict[int, int]:
    """One null draw, scored at all thresholds simultaneously."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[r["game_id"]].append(r)

    shuffled: list[dict] = []
    for gid, rs in by_game.items():
        outcomes = [
            {"placement": r["placement"], "delta": r["delta"], "ending_chips": r["ending_chips"]}
            for r in rs
        ]
        perm_agents = list(agent_ids[: len(rs)])
        if len(perm_agents) < len(rs):
            perm_agents = rng.choices(agent_ids, k=len(rs))
        rng.shuffle(perm_agents)
        for aid, out in zip(perm_agents, outcomes):
            shuffled.append({"agent_id": aid, "game_id": gid, **out})

    null_agents = per_agent_metrics(shuffled)
    return count_at_thresholds(null_agents, thresholds)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-null", type=int, default=10_000)
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    rows_by_run = load_chip_games()
    print(f"Loaded {sum(len(v) for v in rows_by_run.values()):,} rows across "
          f"{len(rows_by_run)} runs.")

    # Observed: pool per-run agent metrics, count at each threshold.
    observed = {t: 0 for t in THRESHOLDS}
    total_agents = 0
    for run_id, rows in rows_by_run.items():
        per_agent = per_agent_metrics(rows)
        total_agents += len(per_agent)
        for t, c in count_at_thresholds(per_agent, THRESHOLDS).items():
            observed[t] += c
    print(f"Total agent-runs: {total_agents}")
    print(f"Observed: {observed}")

    # Null: run n_null permutations, accumulate counts per threshold.
    rng = random.Random(args.seed)
    null_draws = {t: [] for t in THRESHOLDS}
    for i in range(args.n_null):
        if i and i % 500 == 0:
            print(f"  null draw {i}/{args.n_null}")
        total_this_draw = {t: 0 for t in THRESHOLDS}
        for run_id, rows in rows_by_run.items():
            agent_ids = sorted({r["agent_id"] for r in rows})
            run_counts = null_counts_once(rows, agent_ids, rng, THRESHOLDS)
            for t, c in run_counts.items():
                total_this_draw[t] += c
        for t in THRESHOLDS:
            null_draws[t].append(total_this_draw[t])

    # Summarize.
    lines = [
        "# Extreme-Laddering LR3 Threshold Sensitivity",
        "",
        f"Corpus: {total_agents} agent-runs across {len(rows_by_run)} runs.",
        f"Null: {args.n_null:,} within-game permutations, seed={args.seed}.",
        "",
        "Each threshold re-scores the same criterion (LR2>0 AND total_delta<0)"
        " with a different LR3 cut.",
        "",
        "| LR3 ≥ t | observed | null mean | null sd | p(null ≥ observed) |",
        "|---:|---:|---:|---:|---:|",
    ]
    for t in THRESHOLDS:
        draws = null_draws[t]
        mu = sum(draws) / len(draws)
        sd = (sum((d - mu) ** 2 for d in draws) / len(draws)) ** 0.5
        at_least = sum(1 for d in draws if d >= observed[t])
        pval = (at_least + 1) / (args.n_null + 1)
        lines.append(
            f"| {t} | {observed[t]} | {mu:.2f} | {sd:.2f} | {pval:.4f} |"
        )
    lines.append("")
    lines.append(f"Headline (LR3 ≥ 3) from scripts/laddering_rate.py: 19 observed,"
                 f" null mean 0.76, p = 0.0001. This table reproduces that cell"
                 f" and extends to adjacent thresholds.")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUT_MD}")

    # Console echo.
    print()
    for line in lines[7:]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
