"""Integrate the Tier 1 hetero baseline + hetero rep2 runs into the paper data.

When the two Tier 1 runs (hetero baseline, hetero full-env rep2) finish on
Modal, this script:
  1. Verifies they have status=finished on the dashboard
  2. Exports them to .goa_data/runs/ via the dashboard API
  3. Runs the LLM-as-judge classifier on the new agents (resume-safe)
  4. Regenerates all 4 figures + run_summary.csv
  5. Prints the new per-condition taxonomy means + Gini numbers so we can
     spot-check whether anything in §4 needs updating

Idempotent: re-running just refreshes whichever step needs refreshing.

Usage:
    uv run python scripts/integrate_tier1.py
    uv run python scripts/integrate_tier1.py --runs run_rap9xfrst9jyzd
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

from analysis.export_api import export_run  # noqa: E402

# These run IDs are intentionally hard-coded; they're paper-specific
TIER1_RUNS: dict[str, str] = {
    "run_rap9xfrst9jyzd": "exp-heterogeneous-baseline",
    "run_v0ke7u5mt9k84x": "exp-full-heterogeneous-claude-gpt rep2",
}

DASHBOARD = "http://localhost:3000"
TOKEN = "dev-token"
RUNS_DIR = REPO_ROOT / ".goa_data" / "runs"
DATA_DIR = REPO_ROOT / "paper" / "data"


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _check_run_status(run_id: str) -> dict | None:
    """Use the lightweight /runs endpoint and filter — the dashboard endpoint
    can time out for very large runs."""
    try:
        r = httpx.get(
            f"{DASHBOARD}/api/runs",
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        print(f"  ✗ {run_id}: {exc}")
        return None
    if r.status_code != 200:
        return None
    for row in r.json():
        if row.get("run_id") == run_id:
            return row
    return None


def _wait_or_skip(run_ids: list[str]) -> list[str]:
    """Return the subset of run_ids that have status=finished."""
    finished = []
    for rid in run_ids:
        info = _check_run_status(rid)
        if not info:
            print(f"  ✗ {rid}: not visible on dashboard")
            continue
        # The /runs endpoint returns snake_case fields with the
        # tagged-by-backend convention
        status = info.get("status")
        bots = int(info.get("bot_count") or info.get("botCount") or 0)
        games = int(info.get("game_count") or info.get("gameCount") or 0)
        offers = int(info.get("offer_count") or info.get("offerCount") or 0)
        purchases = int(info.get("purchase_count") or info.get("purchaseCount") or 0)
        marker = "✓" if status == "finished" else "◌"
        print(
            f"  {marker} {rid}: status={status}  bots={bots}  games={games}  "
            f"offers={offers}  purch={purchases}"
        )
        if status == "finished":
            finished.append(rid)
    return finished


def _export(run_id: str) -> Path | None:
    target = RUNS_DIR / f"{run_id}.json"
    if target.exists():
        print(f"  ✓ already exported: {target}")
        return target
    print(f"  → exporting {run_id} via Modal API")
    try:
        path = export_run(run_id, out_dir=str(RUNS_DIR))
        return Path(path)
    except SystemExit as exc:
        print(f"  ✗ export failed: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ export error: {exc}")
        return None


def _run_classifier(run_ids: list[str]) -> None:
    print("\n[3/5] Running LLM-as-judge classifier on the new runs (resume-safe)…")
    from scripts.run_taxonomy_classifier import run_batch  # type: ignore

    run_batch(run_ids, dry_run=False, concurrency=8)


def _regenerate_figures() -> None:
    print("\n[4/5] Regenerating figures + run_summary.csv…")
    from scripts.make_figures import (  # type: ignore
        _build_run_summary_table,
        _write_summary_csv,
        fig_coordination_signal,
        fig_gini_comparison,
        fig_marketplace_outcomes,
        fig_taxonomy_heatmap,
    )

    summary = _build_run_summary_table()
    _write_summary_csv(summary)
    fig_taxonomy_heatmap()
    fig_marketplace_outcomes(summary)
    fig_gini_comparison(summary)
    fig_coordination_signal()


def _print_diff_report(run_ids: list[str]) -> None:
    print("\n[5/5] Spot-check report — does §4 need updating?")
    from analysis.loader import load_run  # noqa: E402
    from analysis.metrics import (  # noqa: E402
        compute_agent_stats,
        compute_marketplace_stats,
        gini_coefficient,
    )

    for rid in run_ids:
        path = RUNS_DIR / f"{rid}.json"
        if not path.exists():
            continue
        run = load_run(path)
        stats = compute_agent_stats(run)
        elos = [s.final_elo for s in stats.values()]
        gini = gini_coefficient(elos) if elos else 0.0
        mkt = compute_marketplace_stats(run)
        n_off = mkt.total_offers
        n_pur = mkt.total_purchases
        buy_rate = (n_pur / n_off * 100) if n_off else 0.0
        same_pct = (
            mkt.same_model_purchases
            / (mkt.same_model_purchases + mkt.cross_model_purchases)
            * 100
            if (mkt.same_model_purchases + mkt.cross_model_purchases)
            else 0.0
        )
        cfg_name = (run.config or {}).get("name", "?")
        print(
            f"  {rid}  [{cfg_name}]\n"
            f"    n_agents={len(run.agents)}  n_games={len(run.finished_games)}\n"
            f"    Gini(final ELO)={gini:.4f}\n"
            f"    offers={n_off}  purchases={n_pur}  buy_rate={buy_rate:.1f}%\n"
            f"    in-group purchase rate={same_pct:.0f}% (random expected ~43% in 4C+4G)"
        )

    # Re-print taxonomy means by condition
    csv_path = DATA_DIR / "taxonomy_frequencies.csv"
    if not csv_path.exists():
        return
    print("\n  Updated per-condition taxonomy means (after Tier 1 integration):")
    import csv as _csv

    from analysis.paper_runs import CONDITION_ORDER, condition_map

    PAPER_RUNS = condition_map()
    by_cond: dict[str, list[dict]] = defaultdict(list)
    for r in _csv.DictReader(open(csv_path)):
        cond = PAPER_RUNS.get(r["run_id"])
        if cond:
            by_cond[cond].append(r)
    cats = (
        "competitive_coding",
        "marketplace_exploitation",
        "social_influence",
        "information_exploitation",
        "collusion",
    )
    print(f"  {'condition':<25} {'N':>4} {'comp':>6} {'mkt':>6} {'soc':>6} {'info':>6} {'coll':>6}")
    for cond in CONDITION_ORDER:
        if cond not in by_cond:
            continue
        rows = by_cond[cond]
        means = {c: sum(float(r[c]) for r in rows) / len(rows) for c in cats}
        print(
            f"  {cond:<18} {len(rows):>4} "
            f"{means['competitive_coding']:>6.2f} "
            f"{means['marketplace_exploitation']:>6.2f} "
            f"{means['social_influence']:>6.2f} "
            f"{means['information_exploitation']:>6.2f} "
            f"{means['collusion']:>6.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", nargs="+", help="Specific run IDs (default: TIER1_RUNS)")
    parser.add_argument("--skip-classifier", action="store_true")
    parser.add_argument("--skip-figures", action="store_true")
    args = parser.parse_args()

    _load_env()
    run_ids = args.runs or list(TIER1_RUNS.keys())

    print("[1/5] Checking run status on dashboard…")
    finished = _wait_or_skip(run_ids)
    if not finished:
        print("\nNothing finished yet — nothing to do.")
        sys.exit(0)

    print(f"\n[2/5] Exporting {len(finished)} finished run(s) to {RUNS_DIR}…")
    for rid in finished:
        _export(rid)

    if not args.skip_classifier:
        _run_classifier(finished)

    if not args.skip_figures:
        _regenerate_figures()

    _print_diff_report(finished)


if __name__ == "__main__":
    main()
