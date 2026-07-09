"""Batch analysis: per-condition mean + bootstrap 95% CI for marketplace metrics.

Pulls `/runs/{id}/analysis` for every run in the paper-grade corpus plus the
Apr 16 batch, aggregates by condition, and writes a paper-ready summary.

Usage:
    uv run python scripts/batch_analysis.py
    uv run python scripts/batch_analysis.py --out paper/data/batch_summary.md
    uv run python scripts/batch_analysis.py --skip-missing     # silently drop runs that aren't finished yet

Outputs:
    paper/data/batch_summary.md   — markdown table with mean ± CI per condition
    paper/data/batch_summary.json — machine-readable aggregate

This is the sister script to `scripts/extract_chip_games.py` (which dumps
per-game chip records from local JSONs). This one hits the live API and
only needs the aggregated `/analysis` endpoint, so it runs in <30 seconds
even for the full corpus.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import httpx

API = os.environ.get("GOA_API_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ.get('GOA_API_TOKEN', 'dev-token')}"}
LOCAL_RUNS_DIR = Path(".goa_data/runs")

# (run_id, condition, duration_min)
# Note: this file uses its own labels ("B1 cooperative (6h)", etc.) rather
# than the canonical ones in analysis.paper_runs, because the batch report
# format embeds duration in the condition label. When adding a new run,
# also add it to analysis.paper_runs.PAPER_RUNS.
RUNS: list[tuple[str, str, int]] = [
    # Pre-Apr-16 paper-grade 3h corpus — the ones that support main-text claims
    ("run_1dnl6vtlsiq5pb",  "A1 (no mkt)",            180),
    ("run_x32qdlx4siq1pk",  "B1 cooperative",          180),
    ("run_vskkr3r3ov8jeq",  "B1 cooperative",          60),   # 1h — dropped from 3h pool
    ("run_7w9i4y0spafbbj",  "B1 cooperative",          60),
    ("run_a4ubdvpptkpekw",  "B1 cooperative",          180),
    ("run_isj7ubddsisynd",  "Hetero (full)",           180),
    ("run_v0ke7u5mt9k84x",  "Hetero (full)",           180),
    ("run_lj7qu06ktkpp5e",  "Hetero (full)",           180),
    ("run_rap9xfrst9jyzd",  "Hetero baseline",         180),
    ("run_x62b63l5tkp3cg",  "Hetero baseline",         180),
    ("run_ts13lewssiqa0x",  "Competitive",             180),
    ("run_sub8j16vsiqezi",  "Adversarial",             180),
    ("run_r3drk73gsiqkoa",  "Bad actor",               180),
    ("run_5bcaq5c5skri6t",  "No reviews",              180),
    ("run_pxcchuz7sisn0d",  "Additive (confounded)",   180),

    # Clean additive pre-batch (N=2)
    ("run_57vd316xyfk16q",  "Clean additive",          180),
    ("run_kf2kp5qrz0mmsy",  "Clean additive",          180),

    # 6h B1 reference
    ("run_5ejgk15qzwkqyk",  "B1 cooperative (6h)",     360),

    # Apr 16 19:00Z batch
    ("run_dblkxlpy1udeei",  "Clean additive",          180),
    ("run_ruh1jenp1udfke",  "Clean additive",          180),
    ("run_yrajp77a1udd84",  "B1 cooperative",          180),
    ("run_74ks03yd1udc27",  "B1 cooperative",          180),
    ("run_dxfjtmfa1udf6a",  "No reviews",              180),
    ("run_pkploa771udfhc",  "No reviews + additive",   180),
    ("run_zaeqhzla1udfcf",  "No reviews + additive",   180),
    ("run_4mo6nm0q1udf06",  "Hetero + additive",       180),
    ("run_q4p4v3n51udcmv",  "Hetero + additive",       180),
    ("run_6ajrbhvx1uddt7",  "Clean additive (6h)",     360),
]

METRICS = ("offers", "purchases", "reviews", "chat")


def fetch_api(run_id: str) -> dict | None:
    try:
        r = httpx.get(f"{API}/runs/{run_id}/analysis", headers=HEADERS, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def pull_metric_api(a: dict) -> dict:
    mkt = a.get("marketplace") or {}
    return {
        "status": a.get("status", "?"),
        "source": "api",
        "offers": int(mkt.get("totalOffers") or 0),
        "purchases": int(mkt.get("totalPurchases") or 0),
        "reviews": int(mkt.get("totalReviews") or 0),
        "chat": int(a.get("chatMessageCount") or 0),
        "bots": int(a.get("botsSubmitted") or 0),
    }


def fetch_local(run_id: str) -> dict | None:
    p = LOCAL_RUNS_DIR / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        with p.open() as f:
            d = json.load(f)
        return {
            "status": d.get("status", "?"),
            "source": "local",
            "offers": len(d.get("offers") or {}),
            "purchases": len(d.get("purchases") or {}),
            "reviews": len(d.get("reviews") or {}),
            "chat": len(d.get("comments") or {}),
            "bots": len(d.get("bots") or {}),
        }
    except Exception:
        return None


def fetch_metrics(run_id: str) -> dict | None:
    a = fetch_api(run_id)
    if a is not None:
        return pull_metric_api(a)
    return fetch_local(run_id)


def bootstrap_ci(xs: list[float], n_boot: int = 5000, alpha: float = 0.05, seed: int = 42) -> tuple[float, float]:
    """Percentile bootstrap 95% CI. Returns (lo, hi). For N<2 returns (nan, nan)."""
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    n = len(xs)
    for _ in range(n_boot):
        means.append(sum(rng.choice(xs) for _ in range(n)) / n)
    means.sort()
    lo = means[int(n_boot * alpha / 2)]
    hi = means[int(n_boot * (1 - alpha / 2))]
    return (lo, hi)


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def fmt_ci(lo: float, hi: float) -> str:
    import math
    if math.isnan(lo):
        return "—"
    return f"[{lo:.1f}, {hi:.1f}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="paper/data/batch_summary.md")
    ap.add_argument("--json-out", default="paper/data/batch_summary.json")
    ap.add_argument("--skip-missing", action="store_true",
                    help="Skip runs that the API can't find, rather than listing them")
    ap.add_argument("--min-duration", type=int, default=0,
                    help="Only include runs with duration_min >= this (e.g. 180 for 3h+ only)")
    args = ap.parse_args()

    # Fetch all runs
    records: list[dict] = []
    skipped: list[str] = []
    not_finished: list[str] = []
    for run_id, cond, dur in RUNS:
        if dur < args.min_duration:
            continue
        m = fetch_metrics(run_id)
        if m is None:
            skipped.append(run_id)
            continue
        if m["status"] != "finished":
            not_finished.append(f"{run_id} ({m['status']})")
            if args.skip_missing:
                continue
        records.append({"run_id": run_id, "condition": cond, "duration_min": dur, **m})

    # Group by (condition, duration)
    groups: dict[tuple[str, int], list[dict]] = {}
    for r in records:
        key = (r["condition"], r["duration_min"])
        groups.setdefault(key, []).append(r)

    # Build summary rows
    summary = []
    for (cond, dur), rs in sorted(groups.items()):
        row = {"condition": cond, "duration_min": dur, "N": len(rs)}
        for metric in METRICS:
            xs = [r[metric] for r in rs]
            m = mean(xs)
            lo, hi = bootstrap_ci([float(x) for x in xs])
            row[f"{metric}_mean"] = m
            row[f"{metric}_ci_lo"] = lo
            row[f"{metric}_ci_hi"] = hi
            row[f"{metric}_values"] = xs
        summary.append(row)

    # Write markdown
    lines = [
        "# Batch summary — per-condition marketplace metrics",
        "",
        "Auto-generated by `scripts/batch_analysis.py`.",
        f"Pulled from live API: {API}",
        "",
        "Columns: N = replication count; values are mean across runs; 95% CI is percentile bootstrap (n=5000).",
        "",
        "| Condition | dur (min) | N | offers (mean, 95% CI) | purchases (mean, 95% CI) | reviews | chat |",
        "|---|---:|---:|---|---|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['condition']} | {row['duration_min']} | {row['N']} | "
            f"{row['offers_mean']:.1f} {fmt_ci(row['offers_ci_lo'], row['offers_ci_hi'])} | "
            f"{row['purchases_mean']:.1f} {fmt_ci(row['purchases_ci_lo'], row['purchases_ci_hi'])} | "
            f"{row['reviews_mean']:.1f} | "
            f"{row['chat_mean']:.0f} |"
        )
    lines.append("")
    lines.append("## Raw per-run values")
    lines.append("")
    for row in summary:
        lines.append(f"- **{row['condition']}** ({row['duration_min']}min, N={row['N']}):")
        lines.append(f"  - offers: {row['offers_values']}")
        lines.append(f"  - purchases: {row['purchases_values']}")
    if skipped:
        lines.append("")
        lines.append("## Skipped (API 404)")
        for rid in skipped:
            lines.append(f"- `{rid}`")
    if not_finished and not args.skip_missing:
        lines.append("")
        lines.append("## Not finished (included anyway — partial metrics)")
        for x in not_finished:
            lines.append(f"- {x}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")

    # JSON dump
    json_out = Path(args.json_out)
    json_out.write_text(json.dumps(summary, indent=2, default=float))

    # Console table
    print(f"Runs queried: {len(RUNS) - sum(1 for _,_,d in RUNS if d < args.min_duration)}")
    print(f"Runs with data: {len(records)}")
    print(f"Not finished: {len(not_finished)}")
    print(f"Skipped (404): {len(skipped)}")
    print()
    print(f"{'Condition':<28} {'dur':>5} {'N':>3} {'offers mean (CI)':<28} {'purch mean (CI)':<28}")
    print("-" * 100)
    for row in summary:
        om = row["offers_mean"]
        oc = fmt_ci(row["offers_ci_lo"], row["offers_ci_hi"])
        pm = row["purchases_mean"]
        pc = fmt_ci(row["purchases_ci_lo"], row["purchases_ci_hi"])
        print(f"{row['condition']:<28} {row['duration_min']:>5} {row['N']:>3}  {om:>6.1f} {oc:<20}  {pm:>6.1f} {pc:<20}")

    print()
    print(f"Wrote {out}")
    print(f"Wrote {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
