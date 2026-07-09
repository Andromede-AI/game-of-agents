"""Extract per-game chip data for all paper-grade runs.

Produces a single CSV with per-(run, game, agent) chip records, suitable
for chip-profit analysis without needing to parse the full run JSONs.

Usage:
    uv run python scripts/extract_chip_games.py

Output: paper/data/chip_games.csv
"""

from __future__ import annotations

import csv
from pathlib import Path

from analysis.loader import load_run
from analysis.paper_runs import run_tuple_list


# Paper-grade runs: canonical list lives in analysis.paper_runs.
# Format here: (run_id, condition, duration_str "1h"/"3h"/"6h", rep)
PAPER_RUNS = run_tuple_list()

STARTING_CHIPS = 200
OUT_PATH = Path("paper/data/chip_games.csv")


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    skipped = []

    for run_id, condition, duration, rep in PAPER_RUNS:
        run_path = Path(f".goa_data/runs/{run_id}.json")
        if not run_path.exists():
            skipped.append(run_id)
            continue

        run = load_run(run_path)
        # Map agent_id -> model for this run
        agent_model = {aid: run.agent_model(aid) or "" for aid in run.agent_ids}

        for g in run.finished_games:
            for p in g.participants:
                aid = p.get("agent_id", "")
                ending = p.get("ending_chips", 0) or 0
                rows.append({
                    "run_id": run_id,
                    "condition": condition,
                    "duration": duration,
                    "rep": rep,
                    "game_id": g.game_id,
                    "agent_id": aid,
                    "model": agent_model.get(aid, ""),
                    "bot_id": p.get("bot_id", ""),
                    "seat": p.get("seat", -1),
                    "starting_chips": STARTING_CHIPS,
                    "ending_chips": ending,
                    "delta": ending - STARTING_CHIPS,
                    "placement": p.get("placement", ""),
                    "table_size": g.table_size,
                    "round_count": g.round_count,
                })

    with open(OUT_PATH, "w", newline="") as f:
        fieldnames = [
            "run_id", "condition", "duration", "rep",
            "game_id", "agent_id", "model", "bot_id", "seat",
            "starting_chips", "ending_chips", "delta",
            "placement", "table_size", "round_count",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows):,} rows to {OUT_PATH}")
    print(f"Runs processed: {len(PAPER_RUNS) - len(skipped)}/{len(PAPER_RUNS)}")
    if skipped:
        print(f"Skipped (missing JSON): {skipped}")

    # Summary per run
    print(f"\n=== Per-run summary ===")
    from collections import defaultdict
    by_run = defaultdict(lambda: {"games": set(), "agents": set(), "rows": 0})
    for r in rows:
        by_run[r["run_id"]]["games"].add(r["game_id"])
        by_run[r["run_id"]]["agents"].add(r["agent_id"])
        by_run[r["run_id"]]["rows"] += 1
    for rid, info in by_run.items():
        print(f"  {rid}: {len(info['games'])} games, {len(info['agents'])} agents, {info['rows']} rows")

    print(f"\nFile size: {OUT_PATH.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
