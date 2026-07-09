"""Export run data via the Modal API endpoint (no direct Convex needed).

Fetches the full RunState via `GET /runs/<id>` from the production Modal
backend, then writes it to local JSON in the same format the analysis
pipeline expects.

Usage:
    uv run python -m analysis.export_api <run_id> [--out .goa_data/runs/]

Env vars:
    GOA_API_URL    — backend URL (default: production Modal)
    GOA_API_TOKEN  — API token (default: dev-token)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_API_URL = "http://localhost:8000"


def _api_url() -> str:
    return os.environ.get("GOA_API_URL", DEFAULT_API_URL)


def _headers() -> dict:
    token = os.environ.get("GOA_API_TOKEN", "dev-token")
    return {"Authorization": f"Bearer {token}"}


def export_run(
    run_id: str,
    out_dir: str | Path = ".goa_data/runs",
    sample_size: int = 1000,
    event_limit: int = 500,
) -> Path:
    """Export a run via the Modal API to a local JSON file.

    Uses /runs/<id>/dashboard with sample_full_history=true by default so
    that very large runs (tens of MB of game data) don't truncate the
    HTTP response body. The sample keeps the full agent / bot / offer /
    purchase / comment state and a stratified sample of games adequate
    for our analysis pipeline. Users who genuinely need every game can
    pass sample_size=0 to disable sampling.
    """
    params = ""
    if sample_size and sample_size > 0:
        params = f"?sample_full_history=true&sample_size={sample_size}&event_limit={event_limit}"
    url = f"{_api_url()}/runs/{run_id}/dashboard{params}"
    print(f"Fetching {url}")
    try:
        r = httpx.get(url, headers=_headers(), timeout=300)
    except httpx.RemoteProtocolError as exc:
        # Some runs still exceed the sampled-response size limit — fall
        # back to a smaller sample and try once more.
        if sample_size > 200:
            print(f"  ↻ connection error ({exc}); retrying with sample_size=200")
            return export_run(run_id, out_dir, sample_size=200, event_limit=100)
        raise
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
        sys.exit(1)

    raw = r.json()
    run = raw.get("run", {})
    state = run.get("state", {})
    config = run.get("config", {})

    name = run.get("name") or config.get("name", run_id)
    print(f"Run: {run_id} [{run.get('status', '?')}] — {name}")

    # Counts for confirmation
    print(f"  Agents: {len(state.get('agents', {}))}")
    print(f"  Bots: {len(state.get('bots', {}))}")
    print(f"  Games: {len(state.get('games', {}))}")
    print(f"  Offers: {len(state.get('offers', {}))}")
    print(f"  Purchases: {len(state.get('purchases', {}))}")
    comments_in_state = state.get("comments", {})
    comments_top = raw.get("comments", [])
    print(f"  Comments (state): {len(comments_in_state)}")
    print(f"  Comments (dashboard): {len(comments_top)}")
    print(f"  Snapshots: {len(raw.get('snapshots', []))}")
    print(f"  Events: {len(raw.get('events', []))}")

    # Build the run state in our standard format
    run_state = {
        "run_id": run_id,
        "config": config,
        "status": run.get("status", "unknown"),
        "name": name,
        "created_at": run.get("createdAt"),
        "started_at": run.get("startedAt"),
        "finished_at": run.get("finishedAt"),
        "agents": state.get("agents", {}),
        "bots": state.get("bots", {}),
        "games": state.get("games", {}),
        "offers": state.get("offers", {}),
        "purchases": state.get("purchases", {}),
        "reviews": state.get("reviews", {}),
        "comments": state.get("comments", {}),
        "transcripts": {},  # Not in dashboard endpoint, would need separate fetch
        "final_scores": run.get("finalScores", {}),
        "payouts": run.get("payouts", {}),
        "snapshots": raw.get("snapshots", []),
        "events": raw.get("events", []),
        "dashboard_comments": raw.get("comments", []),
    }

    # Write
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.json"
    with open(out_path, "w") as f:
        json.dump(run_state, f, indent=2, default=str)

    size_kb = out_path.stat().st_size / 1024
    print(f"  Exported: {out_path} ({size_kb:.0f} KB)")
    return out_path


def list_runs() -> list[dict]:
    """List all runs from the API."""
    r = httpx.get(f"{_api_url()}/runs", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export run via Modal API")
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--out", default=".goa_data/runs")
    parser.add_argument("--list", action="store_true", help="List all runs")
    args = parser.parse_args()

    if args.list:
        runs = list_runs()
        print(f"{len(runs)} runs:")
        for r in runs:
            name = r.get("config", {}).get("name", "?")
            status = r.get("status", "?")
            print(f"  {r['run_id']}  [{status:<10}]  {name}")
    elif args.run_id:
        export_run(args.run_id, args.out)
    else:
        parser.print_help()
