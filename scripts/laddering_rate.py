"""Extreme-laddering analysis — larger-N rerun for the original laddering claim.

Definitions (per the 2026-04-17 analysis brief):
    LR2 = P(2nd) + P(3rd) - 2*P(1st)
          middle-biased placement distribution — finishes on the podium
          without winning.
    LR3 = chip_rank - placement_rank
          where placement_rank ranks agents within a run by *mean*
          finishing position (1 = best), and chip_rank ranks by total
          chip delta (1 = best). Both computed across all finished games
          in the run.

An agent is an *extreme ladderer* in a run iff all three hold:
    (a) LR3 >= 3   — chip rank is at least 3 positions worse than placement
    (b) LR2 > 0    — middle-biased placements
    (c) total_delta < 0   — net chip loser across the run

Output:
    findings/ELO_VS_CHIP_COUNT/laddering_per_agent.csv
    findings/ELO_VS_CHIP_COUNT/laddering_summary.md   (observed rate, null
        distribution, p-value)

Usage:
    uv run python scripts/laddering_rate.py
    uv run python scripts/laddering_rate.py --n-null 10000 --seed 17
"""

from __future__ import annotations

import argparse
import csv
import random
import statistics
from collections import defaultdict
from pathlib import Path

CHIP_GAMES_CSV = Path("paper/data/chip_games.csv")
OUT_DIR = Path("findings/ELO_VS_CHIP_COUNT")
OUT_CSV = OUT_DIR / "laddering_per_agent.csv"
OUT_MD = OUT_DIR / "laddering_summary.md"

LR3_THRESHOLD = 3  # the brief's chosen cut; flagged as "a little arbitrary" in brief.


def _rank_with_ties(values: list[float], descending: bool) -> list[float]:
    """Fractional ranks (1 = best). Ties get the mean of the tied positions."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: -values[i] if descending else values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # 1-indexed mean of positions i..j
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def load_chip_games() -> dict[str, list[dict]]:
    """Group per-participant rows by run_id."""
    rows_by_run: dict[str, list[dict]] = defaultdict(list)
    with open(CHIP_GAMES_CSV) as f:
        for row in csv.DictReader(f):
            row["ending_chips"] = int(float(row["ending_chips"])) if row["ending_chips"] else 0
            row["starting_chips"] = int(float(row["starting_chips"]))
            row["delta"] = row["ending_chips"] - row["starting_chips"]
            try:
                row["placement"] = int(float(row["placement"])) if row["placement"] else None
            except ValueError:
                row["placement"] = None
            rows_by_run[row["run_id"]].append(row)
    return rows_by_run


def per_agent_metrics(rows: list[dict]) -> list[dict]:
    """Compute LR2, LR3, total_delta, placements for each agent in one run.

    `rows` is the per-(game, agent) list for a single run. Expects each row to
    have `agent_id`, `placement` (int or None), `delta` (int).
    """
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_agent[r["agent_id"]].append(r)

    agents: list[dict] = []
    for aid, rs in by_agent.items():
        placements = [r["placement"] for r in rs if r["placement"] is not None]
        deltas = [r["delta"] for r in rs]
        n = len(placements)
        if n == 0:
            continue
        p1 = placements.count(1) / n
        p2 = placements.count(2) / n
        p3 = placements.count(3) / n
        lr2 = p2 + p3 - 2 * p1
        mean_place = statistics.fmean(placements)
        agents.append(
            {
                "agent_id": aid,
                "games": n,
                "mean_placement": mean_place,
                "total_delta": sum(deltas),
                "p1": p1, "p2": p2, "p3": p3,
                "lr2": lr2,
            }
        )

    # Rank across agents within the run.
    mean_places = [a["mean_placement"] for a in agents]
    total_deltas = [a["total_delta"] for a in agents]
    place_ranks = _rank_with_ties(mean_places, descending=False)  # low = good
    chip_ranks = _rank_with_ties(total_deltas, descending=True)   # high = good
    for a, pr, cr in zip(agents, place_ranks, chip_ranks):
        a["placement_rank"] = pr
        a["chip_rank"] = cr
        a["lr3"] = cr - pr
        a["extreme_ladderer"] = (
            a["lr3"] >= LR3_THRESHOLD
            and a["lr2"] > 0
            and a["total_delta"] < 0
        )
    return agents


def count_extreme(per_agent: list[dict]) -> int:
    return sum(1 for a in per_agent if a["extreme_ladderer"])


def null_count_once(
    rows: list[dict],
    agent_ids: list[str],
    rng: random.Random,
) -> int:
    """One null draw: within each game, permute which agent occupies which
    (ending_chips, placement) slot. Preserves game-level chip totals and
    placement structure; breaks agent-level consistency.
    """
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
            # Run has more participants per game than distinct agents — shouldn't happen
            # with our data but guard anyway by sampling with replacement.
            perm_agents = rng.choices(agent_ids, k=len(rs))
        rng.shuffle(perm_agents)
        for aid, out in zip(perm_agents, outcomes):
            shuffled.append({"agent_id": aid, "game_id": gid, **out})

    null_agents = per_agent_metrics(shuffled)
    return count_extreme(null_agents)


def bootstrap_pvalue(
    rows: list[dict],
    observed: int,
    n_null: int,
    seed: int,
) -> tuple[float, list[int]]:
    rng = random.Random(seed)
    agent_ids = sorted({r["agent_id"] for r in rows})
    draws = [null_count_once(rows, agent_ids, rng) for _ in range(n_null)]
    # p-value: Pr(null >= observed), one-sided upper tail, with +1 smoothing.
    at_least = sum(1 for d in draws if d >= observed)
    pval = (at_least + 1) / (n_null + 1)
    return pval, draws


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-null", type=int, default=10_000,
                    help="Permutations for null simulation (default 10k).")
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows_by_run = load_chip_games()
    print(f"Loaded {sum(len(v) for v in rows_by_run.values()):,} rows across "
          f"{len(rows_by_run)} runs.")

    all_agents: list[dict] = []
    per_run_summary: list[dict] = []
    all_rows_pooled: list[dict] = []
    for rid, rs in rows_by_run.items():
        agents = per_agent_metrics(rs)
        for a in agents:
            a["run_id"] = rid
            # Attach condition from first row we find.
            a["condition"] = rs[0]["condition"]
            a["duration"] = rs[0]["duration"]
        all_agents.extend(agents)
        all_rows_pooled.extend(rs)
        per_run_summary.append({
            "run_id": rid,
            "condition": rs[0]["condition"],
            "duration": rs[0]["duration"],
            "n_agents": len(agents),
            "n_games": len({r["game_id"] for r in rs}),
            "extreme_count": count_extreme(agents),
        })

    # Persist per-agent CSV.
    with open(OUT_CSV, "w", newline="") as f:
        fieldnames = [
            "run_id", "condition", "duration", "agent_id",
            "games", "mean_placement", "total_delta",
            "p1", "p2", "p3", "lr2",
            "placement_rank", "chip_rank", "lr3",
            "extreme_ladderer",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for a in all_agents:
            writer.writerow({k: a.get(k, "") for k in fieldnames})

    observed_total = sum(1 for a in all_agents if a["extreme_ladderer"])
    print(f"Observed extreme ladderers: {observed_total} / {len(all_agents)} "
          f"agent-run pairs = {observed_total / len(all_agents):.3%}")

    # Null: permute within each run separately, then sum.
    print(f"Running null simulation with n={args.n_null} per run-level shuffle...")
    rng = random.Random(args.seed)
    null_totals: list[int] = []
    for _ in range(args.n_null):
        total = 0
        for rid, rs in rows_by_run.items():
            agent_ids = sorted({r["agent_id"] for r in rs})
            total += null_count_once(rs, agent_ids, rng)
        null_totals.append(total)

    at_least = sum(1 for d in null_totals if d >= observed_total)
    pval = (at_least + 1) / (args.n_null + 1)
    null_mean = statistics.fmean(null_totals)
    null_sd = statistics.pstdev(null_totals) if len(null_totals) > 1 else 0.0
    print(f"Null mean={null_mean:.2f}  sd={null_sd:.2f}  "
          f"p(null >= observed) = {pval:.4f}")

    # Per-condition breakdown.
    per_cond = defaultdict(list)
    for a in all_agents:
        per_cond[a["condition"]].append(a)

    # Write summary markdown.
    lines = [
        "# Extreme Laddering — corpus-wide rerun",
        "",
        f"Re-runs the original extreme-laddering analysis against the full 39-run "
        f"paper corpus (vs the earlier ~25-run cut).",
        "",
        "## Definitions",
        "",
        "- **LR2** = P(2nd) + P(3rd) - 2*P(1st)",
        "- **LR3** = chip_rank - placement_rank (1 = best on both)",
        "- **Extreme ladderer**: LR3 >= 3 AND LR2 > 0 AND total_delta < 0",
        "",
        "## Corpus",
        "",
        f"- Runs: {len(rows_by_run)}",
        f"- Finished games: {len({r['game_id'] for r in all_rows_pooled}):,}",
        f"- Agent-run pairs: {len(all_agents)}",
        "",
        "## Headline",
        "",
        f"- Observed extreme ladderers (pooled across runs): "
        f"**{observed_total} / {len(all_agents)} ({observed_total / len(all_agents):.2%})**",
        f"- Null mean (n={args.n_null:,} draws, within-game permutation): "
        f"**{null_mean:.2f}** (sd {null_sd:.2f})",
        f"- One-sided p(null >= observed): **{pval:.4f}**",
        "",
        "Null permutes (placement, ending_chips) assignment *within each game*, "
        "preserving game-level chip totals and seat structure while breaking "
        "agent-level consistency.",
        "",
        "## Per-run extreme-ladderer counts",
        "",
        "| Run | Condition | Dur | Agents | Games | Extreme |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for s in per_run_summary:
        lines.append(
            f"| `{s['run_id']}` | {s['condition']} | {s['duration']} | "
            f"{s['n_agents']} | {s['n_games']} | {s['extreme_count']} |"
        )

    lines += [
        "",
        "## Per-condition aggregate",
        "",
        "| Condition | Runs | Agent-runs | Extreme | Rate |",
        "|---|---:|---:|---:|---:|",
    ]
    cond_summary = defaultdict(lambda: {"runs": set(), "total": 0, "extreme": 0})
    for a in all_agents:
        cs = cond_summary[a["condition"]]
        cs["runs"].add(a["run_id"])
        cs["total"] += 1
        if a["extreme_ladderer"]:
            cs["extreme"] += 1
    for cond, cs in sorted(cond_summary.items()):
        rate = cs["extreme"] / cs["total"] if cs["total"] else 0
        lines.append(
            f"| {cond} | {len(cs['runs'])} | {cs['total']} | "
            f"{cs['extreme']} | {rate:.2%} |"
        )

    lines += [
        "",
        "## Named extreme ladderers",
        "",
        "| Run | Condition | Agent | LR2 | LR3 | total_delta | P(1,2,3) |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for a in sorted(all_agents, key=lambda x: (x["condition"], x["run_id"], x["agent_id"])):
        if a["extreme_ladderer"]:
            lines.append(
                f"| `{a['run_id']}` | {a['condition']} | {a['agent_id']} | "
                f"{a['lr2']:+.2f} | {a['lr3']:+.1f} | {a['total_delta']:+d} | "
                f"{a['p1']:.2f}/{a['p2']:.2f}/{a['p3']:.2f} |"
            )

    lines += [
        "",
        f"Note: LR3 threshold of 3 is the brief's chosen cut ('a little arbitrary' — "
        f"gaps of 4 or 5 were rare in her smaller sample). With the full "
        f"{len(rows_by_run)}-run corpus, consider revisiting the threshold.",
        "",
        f"Artifacts: `{OUT_CSV}` (per-agent), `{OUT_MD}` (this file).",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n")

    print(f"Wrote {OUT_CSV}")
    print(f"Wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
