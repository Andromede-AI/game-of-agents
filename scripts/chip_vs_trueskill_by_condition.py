"""Per-condition sweep: TrueSkill rank vs chip-profit rank correlation.

Answers a co-author's question: does the previously reported ρ ≈ −0.5 divergence
observed under clean additive settlement appear (a) only there, or
(b) across other conditions too?

Pipeline:
    1. Read per-run chip deltas from `paper/data/chip_games.csv`.
    2. Aggregate to per-(run, agent) total chip profit.
    3. Pull final display rating per agent from the run JSON in `.goa_data/runs/`.
    4. Compute Spearman ρ and Kendall τ per run.
    5. Group by condition; print mean ± (min, max) across runs.

Usage:
    uv run python scripts/chip_vs_trueskill_by_condition.py
    uv run python scripts/chip_vs_trueskill_by_condition.py --out paper/data/chip_vs_trueskill.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path


CHIP_CSV = Path("paper/data/chip_games.csv")
RUNS_DIR = Path(".goa_data/runs")

# Runs we don't have locally but have dashboard exports for under /tmp
# (used as an escape hatch for runs that only live on the new Convex instance
#  and can't be pulled via /runs/{id}/analysis today).
EXTERNAL_RUNS: dict[str, dict] = {
    "run_kf2kp5qrz0mmsy": {
        "condition": "Clean additive",
        "duration": "3h",
        "games_path": Path("/tmp/kf2k/full_games.json"),
        "leaderboard_path": Path("/tmp/kf2k/leaderboard.json"),
    },
    "run_57vd316xyfk16q": {
        "condition": "Clean additive",
        "duration": "3h",
        # games require full participant data which is only in dashboard
        # snapshots for this run; skipped unless a snapshot is placed here.
        "games_path": Path("/tmp/57vd/full_games.json"),
        "leaderboard_path": Path("/tmp/57vd/leaderboard.json"),
    },
}


def spearman_rho(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation between two equal-length lists."""
    n = len(xs)
    if n < 2:
        return float("nan")

    def ranks(vs: list[float]) -> list[float]:
        # Average-rank for ties
        idx = sorted(range(n), key=lambda i: vs[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vs[idx[j + 1]] == vs[idx[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                rk[idx[k]] = avg
            i = j + 1
        return rk

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((v - mx) ** 2 for v in rx))
    dy = math.sqrt(sum((v - my) ** 2 for v in ry))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def kendall_tau(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    c = d = 0
    for i, j in combinations(range(n), 2):
        sx = (xs[i] > xs[j]) - (xs[i] < xs[j])
        sy = (ys[i] > ys[j]) - (ys[i] < ys[j])
        if sx == 0 or sy == 0:
            continue
        if sx == sy:
            c += 1
        else:
            d += 1
    t = c + d
    return (c - d) / t if t else float("nan")


def load_chip_profit_by_run() -> dict[str, dict[str, dict]]:
    """Returns run_id -> {condition, per_agent_profit: {agent_id: total_delta}}."""
    out: dict[str, dict] = {}
    with CHIP_CSV.open() as f:
        for row in csv.DictReader(f):
            rid = row["run_id"]
            if rid not in out:
                out[rid] = {"condition": row["condition"], "profit": defaultdict(float)}
            out[rid]["profit"][row["agent_id"]] += float(row["delta"])
    return out


def external_profit_and_elo(run_id: str) -> tuple[dict[str, float], dict[str, float]] | None:
    """For runs listed in EXTERNAL_RUNS, load chip profit + ELO from side files."""
    spec = EXTERNAL_RUNS.get(run_id)
    if not spec:
        return None
    games_path = spec["games_path"]
    lb_path = spec["leaderboard_path"]
    if not games_path.exists() or not lb_path.exists():
        return None
    try:
        games_data = json.loads(games_path.read_text())
        games = list(games_data.values()) if isinstance(games_data, dict) else games_data
        profit: dict[str, float] = defaultdict(float)
        for g in games:
            if g.get("status") != "finished":
                continue
            for p in g.get("participants") or []:
                aid = p.get("agent_id")
                if aid is None:
                    continue
                profit[aid] += (p.get("ending_chips") or 0) - 200
        lb = json.loads(lb_path.read_text())
        lb_agents = lb.get("agents") or []
        elos = {a["agent_id"]: a.get("best_elo") or 0 for a in lb_agents}
        return dict(profit), elos
    except Exception:
        return None


def final_elo_from_run(run_id: str) -> dict[str, float] | None:
    p = RUNS_DIR / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        with p.open() as f:
            d = json.load(f)
    except Exception:
        return None

    # Look in a few likely locations
    agents = d.get("agents") or {}
    elos: dict[str, float] = {}
    if isinstance(agents, dict):
        for aid, info in agents.items():
            e = info.get("best_elo") or info.get("final_elo") or info.get("elo")
            if e is not None:
                elos[aid] = float(e)
    elif isinstance(agents, list):
        for a in agents:
            aid = a.get("agent_id") or a.get("id")
            e = a.get("best_elo") or a.get("final_elo") or a.get("elo")
            if aid and e is not None:
                elos[aid] = float(e)

    # Fall back: scan final_scores
    if not elos and "final_scores" in d:
        fs = d["final_scores"]
        if isinstance(fs, dict):
            for aid, v in fs.items():
                if isinstance(v, (int, float)):
                    elos[aid] = float(v)
                elif isinstance(v, dict):
                    e = v.get("elo") or v.get("trueskill_mu") or v.get("score")
                    if e is not None:
                        elos[aid] = float(e)

    return elos or None


def per_run_correlation(profit: dict[str, float], elo: dict[str, float]) -> dict | None:
    shared = sorted(set(profit) & set(elo))
    if len(shared) < 3:
        return None
    px = [profit[a] for a in shared]
    ex = [elo[a] for a in shared]
    return {
        "n": len(shared),
        "rho": spearman_rho(px, ex),
        "tau": kendall_tau(px, ex),
        "top_chip": max(shared, key=lambda a: profit[a]),
        "top_elo": max(shared, key=lambda a: elo[a]),
    }


def fmt_mean_range(xs: list[float]) -> str:
    xs = [x for x in xs if not math.isnan(x)]
    if not xs:
        return "—"
    if len(xs) == 1:
        return f"{xs[0]:+.2f}"
    m = sum(xs) / len(xs)
    return f"{m:+.2f} [{min(xs):+.2f}, {max(xs):+.2f}]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="paper/data/chip_vs_trueskill.md")
    args = ap.parse_args()

    runs = load_chip_profit_by_run()

    # Compute per-run correlations
    per_run: list[dict] = []
    missing_elo: list[str] = []
    for rid, info in sorted(runs.items()):
        profit = dict(info["profit"])
        elo = final_elo_from_run(rid)
        if elo is None:
            missing_elo.append(rid)
            continue
        r = per_run_correlation(profit, elo)
        if r is None:
            continue
        per_run.append({
            "run_id": rid,
            "condition": info["condition"],
            **r,
            "top_agree": r["top_chip"] == r["top_elo"],
        })

    # Add external runs (e.g. kf2k, 57vd) not yet in chip_games.csv
    for rid, spec in EXTERNAL_RUNS.items():
        if any(r["run_id"] == rid for r in per_run):
            continue
        result = external_profit_and_elo(rid)
        if result is None:
            continue
        profit, elo = result
        r = per_run_correlation(profit, elo)
        if r is None:
            continue
        per_run.append({
            "run_id": rid,
            "condition": spec["condition"],
            **r,
            "top_agree": r["top_chip"] == r["top_elo"],
        })

    # Aggregate by condition
    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in per_run:
        by_cond[r["condition"]].append(r)

    lines = [
        "# TrueSkill rank vs chip-profit rank — per-condition sweep",
        "",
        "Auto-generated by `scripts/chip_vs_trueskill_by_condition.py`.",
        "",
        "Per-run: Spearman ρ and Kendall τ between final display rating and total chip profit (sum of deltas across finished games).",
        "Negative ρ/τ → the two metrics disagree: agents with high display rating are not the top chip-winners.",
        "",
        "## Aggregate by condition",
        "",
        "| Condition | N runs | mean ρ [min, max] | mean τ [min, max] | top agree (chip#1 = rating#1) |",
        "|---|---:|---|---|---:|",
    ]
    cond_summary = []
    for cond in sorted(by_cond):
        rs = by_cond[cond]
        rhos = [r["rho"] for r in rs]
        taus = [r["tau"] for r in rs]
        agree = sum(1 for r in rs if r["top_agree"])
        lines.append(
            f"| {cond} | {len(rs)} | {fmt_mean_range(rhos)} | {fmt_mean_range(taus)} | {agree}/{len(rs)} |"
        )
        cond_summary.append({
            "condition": cond,
            "n_runs": len(rs),
            "mean_rho": sum(r for r in rhos if not math.isnan(r)) / max(1, sum(1 for r in rhos if not math.isnan(r))),
            "mean_tau": sum(r for r in taus if not math.isnan(r)) / max(1, sum(1 for r in taus if not math.isnan(r))),
            "top_agree_rate": agree / len(rs) if rs else 0,
        })

    lines += [
        "",
        "## Per-run detail",
        "",
        "| Condition | Run | N agents | ρ | τ | Top rating agent | Top chip agent |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for r in sorted(per_run, key=lambda x: (x["condition"], x["run_id"])):
        lines.append(
            f"| {r['condition']} | `{r['run_id']}` | {r['n']} | {r['rho']:+.2f} | {r['tau']:+.2f} | "
            f"{r['top_elo']} | {r['top_chip']} |"
        )

    if missing_elo:
        lines += ["", "## Missing ELO (run JSON not present or schema differs)", ""]
        for rid in missing_elo:
            lines.append(f"- `{rid}`")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")

    # Console summary
    print(f"Runs with both chip + ELO data: {len(per_run)}/{len(runs)}")
    if missing_elo:
        print(f"Missing ELO ({len(missing_elo)}): {missing_elo[:5]}{'...' if len(missing_elo) > 5 else ''}")
    print()
    print(f"{'Condition':<28} {'N':>3} {'mean ρ':>10} {'mean τ':>10} {'top agree':>10}")
    print("-" * 70)
    for c in cond_summary:
        print(f"{c['condition']:<28} {c['n_runs']:>3} {c['mean_rho']:>+10.2f} {c['mean_tau']:>+10.2f} {c['top_agree_rate']:>9.0%}")
    print()
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
