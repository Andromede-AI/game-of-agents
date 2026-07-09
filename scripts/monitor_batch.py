"""Monitor the April 16 parallel experiment batch.

Usage:
    uv run python scripts/monitor_batch.py          # one-shot snapshot
    uv run python scripts/monitor_batch.py --watch  # poll every 5 min until all finished
    uv run python scripts/monitor_batch.py --log runs/batch_apr16_log.jsonl --watch

Writes a status table to stdout and optionally appends JSONL snapshots to a log file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

API = os.environ.get("GOA_API_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ.get('GOA_API_TOKEN', 'dev-token')}"}

# (label, run_id, condition, duration_min)
BATCH = [
    ("add3", "run_dblkxlpy1udeei", "Clean additive",        180),
    ("add4", "run_ruh1jenp1udfke", "Clean additive",        180),
    ("b1r3", "run_yrajp77a1udd84", "B1 cooperative",        180),
    ("b1r4", "run_74ks03yd1udc27", "B1 cooperative",        180),
    ("nr3",  "run_dxfjtmfa1udf6a", "No reviews",            180),
    ("nra1", "run_pkploa771udfhc", "No reviews + additive", 180),
    ("nra2", "run_zaeqhzla1udfcf", "No reviews + additive", 180),
    ("har1", "run_4mo6nm0q1udf06", "Hetero + additive",     180),
    ("har2", "run_q4p4v3n51udcmv", "Hetero + additive",     180),
    ("ac6h", "run_6ajrbhvx1uddt7", "Clean additive (6h)",   360),
    ("op47", "run_uqv8d2ir21mhxq", "Homo Opus 4.7 B1",      180),
    ("hop47", "run_cpvw7kfp21n1qg", "Hetero Opus47/Sonnet46", 180),
]


def fetch_analysis(run_id: str) -> dict | None:
    try:
        r = httpx.get(f"{API}/runs/{run_id}/analysis", headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def snapshot() -> list[dict]:
    rows = []
    for label, rid, cond, dur in BATCH:
        a = fetch_analysis(rid) or {}
        mkt = a.get("marketplace") or {}
        rows.append({
            "ts": datetime.utcnow().isoformat() + "Z",
            "label": label,
            "run_id": rid,
            "condition": cond,
            "duration_min": dur,
            "status": a.get("status", "?"),
            "bots": a.get("botsSubmitted", 0) or 0,
            "offers": mkt.get("totalOffers", 0) or 0,
            "purchases": mkt.get("totalPurchases", 0) or 0,
            "reviews": mkt.get("totalReviews", 0) or 0,
            "chat": a.get("chatMessageCount", 0) or 0,
            "top_elo": round(a.get("topAgentElo", 0) or 0, 0),
        })
    return rows


def render(rows: list[dict]) -> str:
    header = f"{'label':<5} {'run_id':<22} {'condition':<24} {'dur':>4} {'status':<10} {'bots':>5} {'off':>5} {'buy':>5} {'rev':>5} {'chat':>5} {'elo':>6}"
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['label']:<5} {r['run_id']:<22} {r['condition']:<24} {r['duration_min']:>4} "
            f"{r['status']:<10} {r['bots']:>5} {r['offers']:>5} {r['purchases']:>5} "
            f"{r['reviews']:>5} {r['chat']:>5} {r['top_elo']:>6.0f}"
        )
    # Running total of purchases by condition
    by_cond: dict[str, list[int]] = {}
    for r in rows:
        by_cond.setdefault(r["condition"], []).append(r["purchases"])
    lines.append("")
    lines.append("--- running totals (purchases) ---")
    for c, vs in sorted(by_cond.items()):
        lines.append(f"  {c:<24} N={len(vs)}  mean={sum(vs)/len(vs):.1f}  range=[{min(vs)}..{max(vs)}]")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true", help="Poll until all finished")
    ap.add_argument("--interval", type=int, default=300, help="Seconds between polls in --watch mode")
    ap.add_argument("--log", type=Path, help="Append JSONL snapshots to this file")
    args = ap.parse_args()

    while True:
        rows = snapshot()
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
        print(f"\n=== {ts} ===")
        print(render(rows))
        sys.stdout.flush()

        if args.log:
            args.log.parent.mkdir(parents=True, exist_ok=True)
            with args.log.open("a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")

        if not args.watch:
            return 0

        statuses = {r["status"] for r in rows}
        if statuses and all(s in ("finished", "failed") for s in statuses):
            print("\nAll runs finished.")
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
