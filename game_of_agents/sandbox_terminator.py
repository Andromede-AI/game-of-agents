from __future__ import annotations

import asyncio

import modal

from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.logging import configure_logging
from game_of_agents.settlement import compute_marketplace_payouts
from game_of_agents.settings import settings


async def main_async(run_id: str, grace_seconds: int) -> None:
    configure_logging()
    await asyncio.sleep(grace_seconds)
    client = ConvexRuntimeClient(
        settings.convex_url or "",
        site_url=settings.convex_site_url,
        auth_token=settings.convex_sync_token,
    )
    sandboxes = await asyncio.to_thread(client.list_sandboxes, run_id)
    terminated = False
    for sandbox_info in sandboxes:
        sandbox_id = sandbox_info.get("sandboxId") or sandbox_info.get("sandbox_id")
        if not sandbox_id:
            continue
        try:
            sandbox = await modal.Sandbox.from_id.aio(str(sandbox_id))
            exit_code = await sandbox.poll.aio()
            if exit_code is None:
                await sandbox.terminate.aio()
                terminated = True
                await asyncio.to_thread(
                    client.finish_sandbox,
                    run_id,
                    sandbox_id=str(sandbox_id),
                    status="finished",
                    error="terminated after grace period",
                )
        except Exception:
            continue
    dashboard = await asyncio.to_thread(client.get_run_dashboard, run_id)
    if not dashboard or dashboard["run"]["status"] != "stopping":
        return
    final_scores: dict[str, float] = {}
    run_state = dashboard["run"]["state"]
    for agent in run_state.get("agents", {}).values():
        score = float(agent.get("best_rating_score", agent.get("best_elo", 0.0)))
        final_scores[str(agent["agent_id"])] = score
    payouts = compute_marketplace_payouts(
        final_scores,
        run_state.get("purchases", {}).values(),
        dashboard["run"]["config"]["settlement_mode"],
    )
    if terminated:
        await asyncio.to_thread(client.complete_run, run_id, final_scores=final_scores, payouts=payouts)


def main() -> None:
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m game_of_agents.sandbox_terminator <run_id> <grace_seconds>")
    asyncio.run(main_async(sys.argv[1], int(sys.argv[2])))


if __name__ == "__main__":
    main()
