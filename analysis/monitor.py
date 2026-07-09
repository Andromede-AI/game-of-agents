"""Live monitor for running experiments.

Usage:
    uv run python -m analysis.monitor <run_id>
    uv run python -m analysis.monitor <run_id> --poll 15
    uv run python -m analysis.monitor --active          # find and monitor active runs
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, UTC

from convex import ConvexClient
from game_of_agents.settings import Settings


def _client() -> ConvexClient:
    s = Settings()
    if not s.convex_url:
        print("CONVEX_URL not set", file=sys.stderr)
        sys.exit(1)
    return ConvexClient(s.convex_url)


def find_active_runs(c: ConvexClient) -> list[tuple[str, str]]:
    """Find all running experiments by checking known run IDs."""
    import httpx
    import os
    api_url = os.environ.get("GOA_API_URL", "http://localhost:8000")
    api_token = os.environ.get("GOA_API_TOKEN", "dev-token")
    try:
        r = httpx.get(f"{api_url}/runs",
                       headers={"Authorization": f"Bearer {api_token}"}, timeout=5)
        runs = r.json()
        active = []
        for run in runs:
            rid = run.get("run_id", "")
            ctrl = c.query("runtime:getRunControl", {"runId": rid})
            if ctrl and ctrl.get("status") == "running":
                name = ctrl.get("config", {}).get("name", "?")
                active.append((rid, name))
        return active
    except Exception:
        return []


def monitor(run_id: str, poll_interval: int = 20) -> None:
    c = _client()

    ctrl = c.query("runtime:getRunControl", {"runId": run_id})
    if not ctrl:
        print(f"Run {run_id} not found")
        return

    config = ctrl.get("config", {})
    name = config.get("name", "?")
    duration = config.get("duration_minutes", 0)
    agent_ids = [a.get("agent_id", "") for a in config.get("agents", [])]

    print(f"Monitoring: {name} ({run_id})")
    print(f"Duration: {duration} min, Agents: {len(agent_ids)}")
    print(f"Poll: every {poll_interval}s")
    print()

    start_time = time.time()
    last_game_count = 0
    last_offer_count = 0

    while True:
        try:
            ctrl = c.query("runtime:getRunControl", {"runId": run_id})
            status = ctrl.get("status", "?")
            elapsed = (time.time() - start_time) / 60

            # Agent stats
            agent_lines = []
            for aid in agent_ids:
                a = c.query("runtime:getAgentAnalytics", {"runId": run_id, "agentId": aid})
                if a:
                    sd = a.get("self", {})
                    agent_lines.append(
                        f"  {aid:<12} rank=#{sd.get('tournament_rank', '?'):>2} "
                        f"rating={sd.get('best_rating', 0):6.1f} "
                        f"payout={sd.get('projected_payout', 0):6.1f} "
                        f"eq_delta={sd.get('equity_delta', 0):+5.2f}"
                    )

            # Marketplace
            offers = c.query("runtime:listMarketplaceOffers", {"runId": run_id})
            offer_count = len(offers or [])

            # Tournament state for bot/purchase counts
            t_state = c.query("runtime:getTournamentState", {"runId": run_id})
            bot_count = len(t_state.get("bots", [])) if isinstance(t_state, dict) else 0
            purchase_count = len(t_state.get("purchases", [])) if isinstance(t_state, dict) else 0

            # Games (just count, don't fetch full data)
            games = c.query("runtime:queryGames", {
                "runId": run_id, "limit": 1, "scanLimit": 1, "order": "desc"})
            game_count = games.get("scanned", 0) if isinstance(games, dict) else 0

            # Chat
            msgs = c.query("runtime:listRecentMessages", {"runId": run_id, "limit": 3})
            chat_count = len(msgs or [])

            # Print status
            now = datetime.now().strftime("%H:%M:%S")
            new_games = game_count - last_game_count
            new_offers = offer_count - last_offer_count
            last_game_count = game_count
            last_offer_count = offer_count

            print(f"\033[2J\033[H", end="")  # Clear screen
            print(f"{'='*60}")
            print(f" {name} [{status}] — {now} ({elapsed:.0f}m elapsed)")
            print(f"{'='*60}")
            print(f" Games: {game_count:>4}  Bots: {bot_count:>3}  "
                  f"Offers: {offer_count:>3}  Purchases: {purchase_count:>3}  "
                  f"Chat: {chat_count:>3}")
            if new_games > 0 or new_offers > 0:
                print(f" Δ: +{new_games} games, +{new_offers} offers")
            print()
            for line in agent_lines:
                print(line)

            # Recent chat
            if msgs:
                print(f"\n Latest chat:")
                for m in (msgs or [])[-2:]:
                    author = m.get("author_agent_id", m.get("authorAgentId", "?"))
                    text = str(m.get("text", ""))[:70]
                    print(f"   [{author}] {text}")

            # Recent offers
            if offers:
                print(f"\n Marketplace:")
                for o in (offers or [])[-3:]:
                    seller = o.get("sellerAgentId", o.get("seller_agent_id", "?"))
                    title = o.get("title", "?")[:40]
                    price = o.get("pricePct", o.get("price_pct", 0))
                    print(f"   {title} by {seller} @ {price}%")

            if status in ("finished", "failed", "stopping"):
                print(f"\n Run {status}!")
                break

        except KeyboardInterrupt:
            print("\nStopped monitoring.")
            return
        except Exception as e:
            print(f"\n  Error: {e}")

        time.sleep(poll_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor a running experiment")
    parser.add_argument("run_id", nargs="?", help="Run ID to monitor")
    parser.add_argument("--poll", type=int, default=20, help="Poll interval (seconds)")
    parser.add_argument("--active", action="store_true", help="Find and monitor active runs")
    args = parser.parse_args()

    if args.active:
        c = _client()
        active = find_active_runs(c)
        if not active:
            print("No active runs found.")
        elif len(active) == 1:
            monitor(active[0][0], args.poll)
        else:
            print("Active runs:")
            for rid, name in active:
                print(f"  {rid}: {name}")
            monitor(active[0][0], args.poll)
    elif args.run_id:
        monitor(args.run_id, args.poll)
    else:
        parser.print_help()
