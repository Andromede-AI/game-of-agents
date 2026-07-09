"""Export run data from Convex to local JSON for analysis.

In distributed mode, all run data lives in Convex. This script
exports it to the same RunState JSON format the analysis pipeline expects.

Usage:
    uv run python -m analysis.export <run_id> [--out .goa_data/runs/]
    uv run python -m analysis.export --list
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

from convex import ConvexClient
from game_of_agents.settings import Settings


def _client() -> ConvexClient:
    s = Settings()
    if not s.convex_url:
        print("CONVEX_URL not set — check .env", file=sys.stderr)
        sys.exit(1)
    return ConvexClient(s.convex_url)


def _safe_query(c: ConvexClient, name: str, args: dict) -> object:
    """Query with fallback for missing functions."""
    try:
        return c.query(name, args)
    except Exception as e:
        if "Could not find public function" in str(e):
            return None
        raise


def _millis_to_iso(ms) -> str | None:
    """Convert Convex millisecond timestamp to ISO string."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000, tz=UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return str(ms) if ms else None


def _fetch_games(c: ConvexClient, run_id: str) -> list[dict]:
    """Fetch all games using two-pass approach.

    Pass 1: Get all game metadata WITHOUT actions (lightweight, can fetch 200+)
    Pass 2: Fetch actions for each game individually (if capture_actions was on)
    """
    all_games: dict[str, dict] = {}

    # Pass 1: get game summaries without actions (much lighter payload)
    for order in ["desc", "asc"]:
        try:
            result = c.query("runtime:queryGames", {
                "runId": run_id, "limit": 200, "scanLimit": 1000,
                "order": order, "includeActions": False,
            })
            for g in (result.get("games", []) if isinstance(result, dict) else []):
                gid = g.get("game_id", g.get("gameId", ""))
                if gid and gid not in all_games:
                    all_games[gid] = g
        except Exception as e:
            if "Too many bytes" in str(e):
                # Even without actions it's too much — reduce
                try:
                    result = c.query("runtime:queryGames", {
                        "runId": run_id, "limit": 100, "scanLimit": 200,
                        "order": order, "includeActions": False,
                    })
                    for g in (result.get("games", []) if isinstance(result, dict) else []):
                        gid = g.get("game_id", g.get("gameId", ""))
                        if gid and gid not in all_games:
                            all_games[gid] = g
                except Exception:
                    pass
            else:
                raise

    total_summaries = len(all_games)

    # Pass 2: fetch actions in small batches
    # Only if games have empty actions (they would from pass 1)
    game_ids = list(all_games.keys())
    batch_size = 15
    fetched_with_actions = 0

    for i in range(0, len(game_ids), batch_size):
        batch_ids = game_ids[i:i + batch_size]
        try:
            # Re-query with actions for this small batch
            # Use agent filter from first game's participants to narrow
            first_game = all_games[batch_ids[0]]
            participants = first_game.get("participants", [])
            agent_id = participants[0].get("agent_id", "") if participants else None

            result = c.query("runtime:queryGames", {
                "runId": run_id, "limit": batch_size, "scanLimit": batch_size + 5,
                "order": "desc", "includeActions": True,
                **({"agentId": agent_id} if agent_id else {}),
            })
            for g in (result.get("games", []) if isinstance(result, dict) else []):
                gid = g.get("game_id", g.get("gameId", ""))
                if gid in all_games and g.get("actions"):
                    all_games[gid] = g
                    fetched_with_actions += 1
        except Exception:
            continue  # Skip batch on failure

    if fetched_with_actions > 0:
        print(f"    Games: {total_summaries} total, {fetched_with_actions} with actions")

    return list(all_games.values())


def export_run(run_id: str, out_dir: str | Path = ".goa_data/runs") -> Path:
    """Export a run from Convex to a local JSON file."""
    c = _client()

    # 1. Run control (config + status + timestamps)
    ctrl = c.query("runtime:getRunControl", {"runId": run_id})
    if not ctrl:
        print(f"Run {run_id} not found", file=sys.stderr)
        sys.exit(1)

    config = ctrl.get("config", {})
    status = ctrl.get("status", "unknown")
    print(f"Run: {run_id} [{status}]")

    # 2. Tournament state (agents, bots, purchases)
    t_state = _safe_query(c, "runtime:getTournamentState", {"runId": run_id})
    t_state = t_state if isinstance(t_state, dict) else {}

    # Agents
    agents = {}
    for a in t_state.get("agents", []):
        aid = a.get("agent_id", "")
        if aid:
            agents[aid] = a
    # Fallback: if tournament state didn't have agents, use analytics
    if not agents:
        for ac in config.get("agents", []):
            aid = ac.get("agent_id", "")
            analytics = c.query("runtime:getAgentAnalytics", {"runId": run_id, "agentId": aid})
            if analytics:
                sd = analytics.get("self", {})
                agents[aid] = {
                    "agent_id": aid,
                    "runtime": ac.get("runtime", "unknown"),
                    "internet_access": False,
                    "workspace": f"/workspace/{aid}",
                    "best_bot_id": sd.get("best_bot_id"),
                    "best_elo": float(sd.get("best_rating", 0)) * 40 + 1000,
                    "best_rating_mu": float(sd.get("best_rating", 0)),
                    "best_rating_sigma": 8.33,
                    "best_rating_score": float(sd.get("best_rating", 0)),
                    "status": "finished",
                }
    print(f"  Agents: {len(agents)}")

    # Bots
    bots = {}
    for b in t_state.get("bots", []):
        bid = b.get("bot_id", "")
        if bid:
            bots[bid] = b
    print(f"  Bots: {len(bots)}")

    # Purchases
    purchases = {}
    for p in t_state.get("purchases", []):
        pid = p.get("purchase_id", "")
        if pid:
            purchases[pid] = p
    print(f"  Purchases: {len(purchases)}")

    # 3. Games (with actions)
    raw_games = _fetch_games(c, run_id)
    games = {}
    for g in raw_games:
        gid = g.get("game_id", g.get("gameId", ""))
        if gid:
            games[gid] = g
    print(f"  Games: {len(games)}")

    # 4. Marketplace offers
    # The Convex query `runtime:listMarketplaceOffers` defaults to limit=50
    # (capped server-side at 200). Paper-grade 3h runs observed up to 99
    # offers, so we request the server cap explicitly. If a future run
    # actually hits the cap we print a warning; true cursor pagination
    # would require a Convex function change (follow-up, see Task #94).
    offers = {}
    offers_raw = _safe_query(
        c, "runtime:listMarketplaceOffers", {"runId": run_id, "limit": 200}
    )
    if offers_raw and len(offers_raw) >= 200:
        print(
            f"  WARN: offers query hit server cap of 200 for {run_id}; "
            "pagination is incomplete — add cursor support to "
            "convex/runtime.ts:listMarketplaceOffers before trusting the count.",
            file=sys.stderr,
        )
    for o in (offers_raw or []):
        oid = o.get("offer_id", o.get("offerId", ""))
        if oid:
            offers[oid] = {
                "offer_id": oid,
                "seller_agent_id": o.get("seller_agent_id", o.get("sellerAgentId", "")),
                "bot_id": o.get("bot_id", o.get("botId", "")),
                "title": o.get("title", ""),
                "description": o.get("description", ""),
                "evidence": o.get("evidence", ""),
                "price_pct": float(o.get("price_pct", o.get("pricePct", 0))),
                "artifact_paths": o.get("artifact_paths", o.get("artifactPaths", [])),
                "review_count": int(o.get("review_count", o.get("reviewCount", 0))),
                "created_at": o.get("created_at", o.get("createdAt", "")),
            }
    print(f"  Offers: {len(offers)}")

    # 5. Chat messages
    comments = {}
    msgs = _safe_query(c, "runtime:listRecentMessages", {"runId": run_id, "limit": 200})
    for cm in (msgs or []):
        cid = cm.get("message_id", cm.get("messageId", cm.get("commentId", "")))
        if cid:
            comments[cid] = {
                "message_id": cid,
                "author_agent_id": cm.get("author_agent_id", cm.get("authorAgentId", "")),
                "commentator_id": cm.get("commentator_id", cm.get("commentatorId", "")),
                "text": cm.get("text", ""),
                "sequence": int(cm.get("sequence", 0)),
                "parent_message_id": cm.get("parent_message_id", cm.get("parentMessageId")),
                "created_at": cm.get("created_at", cm.get("createdAt", "")),
            }
    print(f"  Chat: {len(comments)}")

    # 6. Agent transcripts (reasoning traces)
    transcripts: dict[str, list] = {}
    for ac in config.get("agents", []):
        aid = ac.get("agent_id", "")
        if not aid:
            continue
        conv = _safe_query(c, "runtime:getAgentConversation", {"runId": run_id, "agentId": aid, "limit": 500})
        if conv:
            transcripts[aid] = list(conv)
    total_turns = sum(len(v) for v in transcripts.values())
    print(f"  Transcripts: {len(transcripts)} agents, {total_turns} total turns")

    # 7. Final scores and payouts (from analytics which computes settlement)
    final_scores = {}
    payouts = {}
    for ac in config.get("agents", []):
        aid = ac.get("agent_id", "")
        if not aid:
            continue
        analytics = _safe_query(c, "runtime:getAgentAnalytics", {"runId": run_id, "agentId": aid})
        if analytics:
            sd = analytics.get("self", {})
            final_scores[aid] = float(sd.get("best_rating", 0))
            payouts[aid] = float(sd.get("projected_payout", sd.get("best_rating", 0)))

    # Build RunState
    run_state = {
        "run_id": run_id,
        "config": config,
        "status": status,
        "created_at": _millis_to_iso(ctrl.get("createdAt") or ctrl.get("startedAt")),
        "started_at": _millis_to_iso(ctrl.get("startedAt")),
        "finished_at": _millis_to_iso(ctrl.get("finishedAt")),
        "agents": agents,
        "bots": bots,
        "games": games,
        "offers": offers,
        "purchases": purchases,
        "reviews": {},
        "comments": comments,
        "transcripts": transcripts,
        "final_scores": final_scores,
        "payouts": payouts,
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export run from Convex")
    parser.add_argument("run_id", nargs="?", help="Run ID to export")
    parser.add_argument("--out", default=".goa_data/runs", help="Output directory")
    args = parser.parse_args()

    if args.run_id:
        export_run(args.run_id, args.out)
    else:
        parser.print_help()
