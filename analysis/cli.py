"""Experiment CLI — single entry point for all experiment operations.

Usage:
    uv run python -m analysis.cli run configs/exp_full_homo_claude.yaml
    uv run python -m analysis.cli status
    uv run python -m analysis.cli export <run_id>
    uv run python -m analysis.cli analyze <run_id>
    uv run python -m analysis.cli monitor <run_id>
    uv run python -m analysis.cli compare label=run_id ...
    uv run python -m analysis.cli figures
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import yaml
from convex import ConvexClient

from game_of_agents.settings import Settings
from analysis.runs import register, update_status, list_runs, get_run


import os
API = os.environ.get("GOA_API_URL", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ.get('GOA_API_TOKEN', 'dev-token')}"}


def _convex() -> ConvexClient:
    s = Settings()
    if not s.convex_url:
        print("CONVEX_URL not set — check .env", file=sys.stderr)
        sys.exit(1)
    return ConvexClient(s.convex_url)


def _api_ok() -> bool:
    try:
        httpx.get(f"{API}/runs", headers=HEADERS, timeout=3)
        return True
    except Exception:
        return False


# ── run ──────────────────────────────────────────────────────

def cmd_run(args):
    """Create and start an experiment, then poll until done."""
    if not _api_ok():
        print("API server not running. Start with: uv run goa serve")
        sys.exit(1)

    config = yaml.safe_load(open(args.config))
    name = config.get("name", args.config)
    duration = config.get("duration_minutes", 60)

    r = httpx.post(f"{API}/runs", json=config, headers=HEADERS, timeout=30)
    r.raise_for_status()
    run_id = r.json()["run_id"]
    register(run_id, args.config, name)
    print(f"Created {name}: {run_id} ({duration} min)")

    print("Starting sandboxes...")
    try:
        httpx.post(f"{API}/runs/{run_id}/start", headers=HEADERS, timeout=300)
        update_status(run_id, "running")
        print("Started!")
    except httpx.ReadTimeout:
        update_status(run_id, "running")
        print("Start timed out (sandboxes spawning — normal)")

    if args.no_wait:
        print(f"\nRun ID: {run_id}")
        print(f"Monitor: uv run python -m analysis.cli monitor {run_id}")
        return

    _poll_until_done(run_id, name, args.poll)

    if not args.no_analyze:
        print("\nExporting...")
        path = _export(run_id)
        print("\nAnalyzing...")
        _analyze(path)


# ── status ───────────────────────────────────────────────────

def cmd_status(args):
    """Show status of all known runs, refreshing from Convex."""
    c = _convex()
    entries = list_runs()

    if not entries:
        print("No runs registered. Launch one with: uv run python -m analysis.cli run <config>")
        return

    print(f"{'Run ID':<28} {'Status':<10} {'Name':<32} {'Bots':>5} {'Offers':>7} {'Buys':>5}")
    print("-" * 95)

    for entry in entries:
        rid = entry["run_id"]
        name = entry.get("name", "?")[:30]
        try:
            ctrl = c.query("runtime:getRunControl", {"runId": rid})
            live_status = ctrl.get("status", "?") if ctrl else "not found"
            if live_status != entry.get("status"):
                update_status(rid, live_status)

            offers = c.query("runtime:listMarketplaceOffers", {"runId": rid})
            t = c.query("runtime:getTournamentState", {"runId": rid})
            n_bots = len(t.get("bots", [])) if isinstance(t, dict) else 0
            n_offers = len(offers or [])
            n_buys = len(t.get("purchases", [])) if isinstance(t, dict) else 0
            print(f"{rid:<28} {live_status:<10} {name:<32} {n_bots:>5} {n_offers:>7} {n_buys:>5}")
        except Exception:
            print(f"{rid:<28} {'error':<10} {name:<32}")

    # Show exported data
    exported = list(Path(".goa_data/runs").glob("*.json")) if Path(".goa_data/runs").exists() else []
    if exported:
        exported_ids = {p.stem for p in exported}
        registered_ids = {e["run_id"] for e in entries}
        unregistered = exported_ids - registered_ids
        if unregistered:
            print(f"\n  ({len(unregistered)} exported runs not in registry — use 'analyze' with run_id directly)")


# ── monitor ──────────────────────────────────────────────────

def cmd_monitor(args):
    """Live monitor a running experiment."""
    from analysis.monitor import monitor
    monitor(args.run_id, poll_interval=args.poll)


# ── export ───────────────────────────────────────────────────

def cmd_export(args):
    """Export run data from Convex to local JSON."""
    path = _export(args.run_id)
    update_status(args.run_id, "exported")
    print(f"\nAnalyze: uv run python -m analysis.cli analyze {args.run_id}")


# ── analyze ──────────────────────────────────────────────────

def cmd_analyze(args):
    """Run analysis on an exported run."""
    path = Path(f".goa_data/runs/{args.run_id}.json")
    if not path.exists():
        print(f"Not exported yet. Run: uv run python -m analysis.cli export {args.run_id}")
        sys.exit(1)
    _analyze(path)


# ── compare ──────────────────────────────────────────────────

def cmd_compare(args):
    """Compare multiple exported runs."""
    from analysis.loader import load_run
    from analysis.compare import summarize_condition, print_comparison

    summaries = []
    for label_path in args.runs:
        parts = label_path.split("=", 1)
        if len(parts) == 2:
            label, run_id = parts
        else:
            run_id = parts[0]
            entry = get_run(run_id)
            label = entry.get("name", run_id) if entry else run_id

        path = Path(f".goa_data/runs/{run_id}.json")
        if not path.exists():
            print(f"Not exported: {run_id} — run 'export' first")
            continue
        run = load_run(path)
        summaries.append(summarize_condition(label, [run]))

    if summaries:
        print_comparison(summaries)


# ── figures ──────────────────────────────────────────────────

def cmd_figures(args):
    """Generate paper figures from all exported runs."""
    from analysis.loader import load_run
    from analysis.plots import plot_rating_trajectories, plot_aggression_heatmap, plot_marketplace_flow
    from analysis.metrics import compute_agent_stats, gini_coefficient

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    exported = sorted(Path(".goa_data/runs").glob("*.json")) if Path(".goa_data/runs").exists() else []
    if not exported:
        print("No exported runs found. Export runs first.")
        return

    for path in exported:
        run = load_run(path)
        rid_short = run.run_id[-8:]
        name = run.config.get("name", "")
        stats = compute_agent_stats(run)
        gini = gini_coefficient([s.final_elo for s in stats.values()])
        n_offers = len(run.offers)
        print(f"{path.stem}: {name} | {len(run.finished_games)} games | Gini={gini:.3f} | {n_offers} offers")

        prefix = f"{out}/{path.stem}"
        try:
            plot_rating_trajectories(run, f"{prefix}_ratings.png")
            plot_aggression_heatmap(run, f"{prefix}_aggression.png")
            if run.purchases:
                plot_marketplace_flow(run, f"{prefix}_marketplace.png")
        except Exception as e:
            print(f"  Plot error: {e}")

    print(f"\nFigures saved to {out}/")


# ── helpers ──────────────────────────────────────────────────

def _poll_until_done(run_id: str, name: str, interval: int = 30):
    c = _convex()
    start = time.time()
    last_status = ""

    while True:
        try:
            ctrl = c.query("runtime:getRunControl", {"runId": run_id})
            status = ctrl.get("status", "?") if ctrl else "?"
            elapsed = (time.time() - start) / 60

            if status != last_status:
                print(f"  [{elapsed:.0f}m] {name}: {status}")
                last_status = status
                update_status(run_id, status)

            if status in ("finished", "failed"):
                print(f"\n{name} {status} after {elapsed:.0f} min")
                return

            offers = c.query("runtime:listMarketplaceOffers", {"runId": run_id})
            t = c.query("runtime:getTournamentState", {"runId": run_id})
            n_bots = len(t.get("bots", [])) if isinstance(t, dict) else 0
            n_offers = len(offers or [])
            n_buys = len(t.get("purchases", [])) if isinstance(t, dict) else 0
            print(f"  [{elapsed:.0f}m] bots={n_bots} offers={n_offers} purchases={n_buys}",
                  end="\r", flush=True)

        except KeyboardInterrupt:
            print(f"\nStopped polling. Run still active: {run_id}")
            return
        except Exception as e:
            print(f"  poll error: {e}")

        time.sleep(interval)


def _export(run_id: str) -> Path:
    from analysis.export import export_run
    return export_run(run_id)


def _analyze(path: Path):
    from analysis.loader import load_run
    from analysis.metrics import print_summary
    run = load_run(path)
    print_summary(run)


# ── main ─────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(prog="analysis.cli", description="Experiment CLI")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="Launch an experiment")
    r.add_argument("config", help="Config YAML")
    r.add_argument("--poll", type=int, default=30)
    r.add_argument("--no-wait", action="store_true")
    r.add_argument("--no-analyze", action="store_true")

    sub.add_parser("status", help="Show all runs")

    m = sub.add_parser("monitor", help="Live monitor")
    m.add_argument("run_id")
    m.add_argument("--poll", type=int, default=20)

    e = sub.add_parser("export", help="Export from Convex")
    e.add_argument("run_id")

    a = sub.add_parser("analyze", help="Analyze exported run")
    a.add_argument("run_id")

    c = sub.add_parser("compare", help="Compare runs")
    c.add_argument("runs", nargs="+", help="label=run_id pairs")

    f = sub.add_parser("figures", help="Generate paper figures")
    f.add_argument("--out", default="paper/figures/generated")

    rp = sub.add_parser("report", help="Generate HTML report")
    rp.add_argument("run_id", nargs="?", help="Run ID (or 'all' for all exported runs)")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    cmds = {"run": cmd_run, "status": cmd_status, "monitor": cmd_monitor,
            "export": cmd_export, "analyze": cmd_analyze, "compare": cmd_compare,
            "figures": cmd_figures}

    # report command
    def cmd_report(a):
        from analysis.report import generate_report
        from analysis.loader import load_run
        rid = a.run_id
        path = Path(f".goa_data/runs/{rid}.json")
        if not path.exists():
            print(f"Not exported: {rid}")
            sys.exit(1)
        run = load_run(path)
        html = generate_report(run)
        out = Path(".goa_data/reports")
        out.mkdir(parents=True, exist_ok=True)
        out_path = out / f"{rid}.html"
        out_path.write_text(html)
        print(f"Report: {out_path}")

    cmds["report"] = cmd_report
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
