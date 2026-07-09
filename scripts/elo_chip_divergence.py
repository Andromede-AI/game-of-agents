#!/usr/bin/env python3
"""Hunt for specific ELO vs chip-delta divergence patterns in match data.

Patterns sought:
  1. Laddering — agents with strong placement average (many 2nds) but weak
     chip_delta (never winning the pot).
  2. Matchmaking shielding — weak bots whose chip losses are smaller than
     expected because TrueSkill spread kept them away from the strongest bots.
  3. Late-blooming — agents whose last N games are strong (rating climbs)
     but cumulative chip_delta stays negative because early games were bad.

Run:
    uv run python scripts/elo_chip_divergence.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev


RUNS_OF_INTEREST = [
    "run_57vd316xyfk16q",  # exp-additive-clean (largest w/ 176 bots)
    "run_9zg93zgqx9mlg5",  # exp-additive-neutral-prompt
    "run_e8jk0hj3yado2c",  # exp-additive-clean
    "run_la7sz5mez0e5n0",  # exp-additive-clean
    "run_hzqrv9mnxim4qi",  # exp-additive-neutral-prompt
    "run_kf2kp5qrz0mmsy",  # exp-additive-clean
]


def load(run_id: str) -> dict | None:
    p = Path(f".goa_data/runs/{run_id}.json")
    if not p.exists():
        return None
    return json.load(p.open())


def games_sorted(run: dict) -> list[dict]:
    gs = [g for g in run["games"].values() if g.get("status") == "finished"]
    gs.sort(key=lambda g: g.get("finished_at") or g.get("started_at") or 0)
    return gs


def chip_delta(p: dict, starting_stack: float) -> float:
    return (p.get("ending_chips") or 0) - starting_stack


def per_agent_aggregate(games: list[dict], starting_stack: float) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {
        "placements": [], "deltas": [], "n": 0, "wins": 0,
    })
    for g in games:
        for p in g["participants"]:
            aid = p["agent_id"]
            out[aid]["placements"].append(p["placement"])
            out[aid]["deltas"].append(chip_delta(p, starting_stack))
            out[aid]["n"] += 1
            if p["placement"] == 1:
                out[aid]["wins"] += 1
    return out


def find_laddering(agg: dict[str, dict]) -> list[tuple]:
    """Agents with mean placement < 2.5 (better than random) but mean
    chip_delta < 0 (losing chips). Characteristic: many 2nd/3rd places,
    few 1st places."""
    hits = []
    for aid, s in agg.items():
        if s["n"] < 10:
            continue
        mp = mean(s["placements"])
        md = mean(s["deltas"])
        win_rate = s["wins"] / s["n"]
        # Laddering: better-than-random placement but still losing chips
        # AND a low win rate (< 0.2) vs what you'd expect for good placement
        if mp < 2.5 and md < 0:
            hits.append((aid, mp, md, win_rate, s["n"], s["wins"]))
    return sorted(hits, key=lambda r: r[1])  # best placement first


def find_matchmaking_shield(run: dict, games: list[dict]) -> list[dict]:
    """For each (weak) agent, compute how often they played the top-ELO
    agent vs how often they played other weak agents. Also compute their
    chip-loss rate against each tier."""
    agents = run["agents"]
    # Final ELO per agent (best_elo from agent state)
    agent_elo = {
        aid: a.get("best_elo", 1000.0) for aid, a in agents.items()
    }
    elo_sorted = sorted(agent_elo.items(), key=lambda kv: kv[1], reverse=True)
    top_k = {aid for aid, _ in elo_sorted[:2]}
    bottom_k = {aid for aid, _ in elo_sorted[-2:]}
    middle_k = set(agent_elo) - top_k - bottom_k

    start = run["config"]["game"]["starting_stack"]

    # For each agent, count how many games they played against top/middle/bottom
    # and their mean chip_delta in those games
    out = []
    for aid in agent_elo:
        buckets = {"top": [], "middle": [], "bottom": []}
        for g in games:
            participants = g["participants"]
            my = next((p for p in participants if p["agent_id"] == aid), None)
            if my is None:
                continue
            opponents = [p["agent_id"] for p in participants if p["agent_id"] != aid]
            d = chip_delta(my, start)
            # Which bucket has the strongest opponent in this match?
            has_top = any(o in top_k for o in opponents)
            has_bot = any(o in bottom_k for o in opponents)
            if has_top:
                buckets["top"].append(d)
            elif has_bot and not has_top:
                buckets["bottom"].append(d)
            else:
                buckets["middle"].append(d)
        out.append({
            "agent_id": aid,
            "elo": agent_elo[aid],
            "tier": "top" if aid in top_k else ("bottom" if aid in bottom_k else "middle"),
            "vs_top": buckets["top"],
            "vs_mid": buckets["middle"],
            "vs_bot": buckets["bottom"],
        })
    return out


def find_late_bloomer(games: list[dict], starting_stack: float) -> list[tuple]:
    """Compare each agent's early-half placement to late-half placement.
    Flag agents whose late half is much better than early half
    (positive improvement) but whose cumulative chip_delta is still bad."""
    # Sort games chronologically (already done)
    half = len(games) // 2
    early = games[:half]
    late = games[half:]

    early_stats: dict[str, list] = defaultdict(list)
    late_stats: dict[str, list] = defaultdict(list)
    cum_delta: dict[str, float] = defaultdict(float)
    total_n: dict[str, int] = defaultdict(int)

    for g in early:
        for p in g["participants"]:
            early_stats[p["agent_id"]].append((p["placement"], chip_delta(p, starting_stack)))
            cum_delta[p["agent_id"]] += chip_delta(p, starting_stack)
            total_n[p["agent_id"]] += 1
    for g in late:
        for p in g["participants"]:
            late_stats[p["agent_id"]].append((p["placement"], chip_delta(p, starting_stack)))
            cum_delta[p["agent_id"]] += chip_delta(p, starting_stack)
            total_n[p["agent_id"]] += 1

    results = []
    for aid in set(early_stats) | set(late_stats):
        if len(early_stats[aid]) < 5 or len(late_stats[aid]) < 5:
            continue
        early_place = mean(p for p, _ in early_stats[aid])
        late_place = mean(p for p, _ in late_stats[aid])
        early_delta = mean(d for _, d in early_stats[aid])
        late_delta = mean(d for _, d in late_stats[aid])
        improvement = early_place - late_place  # positive = got better
        results.append({
            "agent_id": aid,
            "early_place": early_place, "late_place": late_place,
            "early_delta": early_delta, "late_delta": late_delta,
            "improvement": improvement,
            "total_delta": cum_delta[aid],
            "n_early": len(early_stats[aid]), "n_late": len(late_stats[aid]),
        })
    return results


def print_run_report(run_id: str, run: dict) -> None:
    cfg = run["config"]
    games = games_sorted(run)
    if not games:
        print(f"\n=== {run_id}: no games, skipping")
        return
    start = cfg["game"]["starting_stack"]
    agg = per_agent_aggregate(games, start)
    agents = run["agents"]

    print(f"\n{'='*78}")
    print(f"Run {run_id}  ({cfg.get('name')})  games={len(games)}  agents={len(agents)}")
    print(f"{'='*78}")

    # Summary table
    print(f"\n{'agent':<10} {'elo':>7} {'mean_plc':>9} {'mean_Δ':>9} {'total_Δ':>9} "
          f"{'wins':>5} {'win%':>6} {'n':>5}")
    print("-" * 72)
    rows = []
    for aid, s in agg.items():
        elo = agents[aid].get("best_elo", 0)
        mp = mean(s["placements"])
        md = mean(s["deltas"])
        td = sum(s["deltas"])
        wr = s["wins"] / s["n"] if s["n"] else 0
        rows.append((aid, elo, mp, md, td, s["wins"], wr, s["n"]))
    for aid, elo, mp, md, td, wins, wr, n in sorted(rows, key=lambda r: -r[1]):
        print(f"{aid:<10} {elo:>7.1f} {mp:>9.2f} {md:>+9.1f} {td:>+9.0f} "
              f"{wins:>5d} {wr*100:>5.1f}% {n:>5d}")

    # (1) Laddering
    ladder = find_laddering(agg)
    print(f"\n  [1] LADDERING  (mean placement < 2.5 AND chips negative)")
    if ladder:
        for aid, mp, md, wr, n, wins in ladder:
            print(f"    {aid}: mean_placement={mp:.2f}  mean_Δ={md:+.1f}  "
                  f"win_rate={wr*100:.1f}% ({wins}/{n})  ELO={agents[aid].get('best_elo'):.0f}")
    else:
        print("    (none — no agents had this pattern)")

    # (2) Matchmaking shielding
    shield = find_matchmaking_shield(run, games)
    print(f"\n  [2] MATCHMAKING SHIELDING  (bottom-tier chip deltas by opponent tier)")
    for entry in sorted(shield, key=lambda e: e["elo"]):
        if entry["tier"] != "bottom":
            continue
        vt = entry["vs_top"]; vm = entry["vs_mid"]; vb = entry["vs_bot"]
        print(f"    {entry['agent_id']} (ELO={entry['elo']:.0f}, bottom tier):")
        print(f"      vs TOP agents: n={len(vt):>2}  mean_Δ={mean(vt) if vt else 0:+.1f}")
        print(f"      vs MID agents: n={len(vm):>2}  mean_Δ={mean(vm) if vm else 0:+.1f}")
        print(f"      vs BOT agents: n={len(vb):>2}  mean_Δ={mean(vb) if vb else 0:+.1f}")

    # Also show top-tier exposure to bottom
    print(f"    --- top-tier exposure to bottom ---")
    for entry in sorted(shield, key=lambda e: -e["elo"]):
        if entry["tier"] != "top":
            continue
        vt = entry["vs_top"]; vm = entry["vs_mid"]; vb = entry["vs_bot"]
        print(f"    {entry['agent_id']} (ELO={entry['elo']:.0f}, top tier):")
        print(f"      vs TOP: n={len(vt):>2}  vs MID: n={len(vm):>2}  vs BOT: n={len(vb):>2}")

    # (3) Late-blooming
    late = find_late_bloomer(games, start)
    late_improved = [r for r in late if r["improvement"] > 0.3]  # >0.3 avg placement improvement
    deserved_but_failed = [r for r in late_improved if r["total_delta"] < -200]
    print(f"\n  [3] LATE-BLOOMER  (early vs late-half placement, with total_Δ)")
    if late_improved:
        for r in sorted(late_improved, key=lambda r: -r["improvement"]):
            badge = "  <<DESERVED BUT NEGATIVE CHIPS" if r["total_delta"] < -200 else ""
            print(f"    {r['agent_id']}: early_plc={r['early_place']:.2f} → late_plc={r['late_place']:.2f} "
                  f"(Δ={r['improvement']:+.2f})  "
                  f"total_chips={r['total_delta']:+.0f}  "
                  f"ELO={agents[r['agent_id']].get('best_elo'):.0f}{badge}")
    else:
        print("    (none — no agent improved by >0.3 placement between halves)")


def main():
    for run_id in RUNS_OF_INTEREST:
        run = load(run_id)
        if run is None:
            print(f"(skip {run_id}: not found)")
            continue
        print_run_report(run_id, run)


if __name__ == "__main__":
    main()
