from __future__ import annotations

import asyncio
from typing import Any

from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.model_resolver import resolve_agent_config
from game_of_agents.models import RunConfig, RunState, RunStatus
from game_of_agents.sandbox_spawner import (
    spawn_agent_sandbox,
    spawn_terminator_sandbox,
    spawn_tournament_sandbox,
)
from game_of_agents.settings import settings


class DistributedRunManager:
    def __init__(self) -> None:
        if not settings.convex_url:
            raise RuntimeError("DistributedRunManager requires CONVEX_URL")
        self.runtime = ConvexRuntimeClient(
            settings.convex_url,
            site_url=settings.convex_site_url,
            auth_token=settings.convex_sync_token,
        )

    async def create_run(self, config: RunConfig) -> RunState:
        config = config.model_copy(update={"agents": [resolve_agent_config(a) for a in config.agents]})
        run_id = await asyncio.to_thread(self.runtime.create_run, config)
        return await self.get_run(run_id, full=False)

    async def list_runs(self) -> list[RunState]:
        items = await asyncio.to_thread(self.runtime.list_runs)
        runs = [self.runtime.summary_to_run_state(item) for item in items]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        return runs

    async def get_run(self, run_id: str, *, full: bool = False) -> RunState:
        if full:
            dashboard = await asyncio.to_thread(self.runtime.get_run_dashboard, run_id)
            if dashboard is None:
                raise KeyError(run_id)
            return self.runtime.dashboard_to_run_state(dashboard)
        summary = await asyncio.to_thread(self.runtime.get_run_summary, run_id)
        if summary is None:
            raise KeyError(run_id)
        return self.runtime.summary_to_run_state(summary)

    async def get_run_dashboard(
        self,
        run_id: str,
        *,
        snapshot_limit: int = 800,
        event_limit: int = 200,
        sample_full_history: bool = False,
        sample_size: int = 320,
    ) -> dict[str, Any]:
        if sample_full_history:
            dashboard = await asyncio.to_thread(
                self.runtime.get_run_dashboard_sampled,
                run_id,
                sample_size=sample_size,
                event_limit=event_limit,
            )
        else:
            dashboard = await asyncio.to_thread(
                self.runtime.get_run_dashboard,
                run_id,
                snapshot_limit=snapshot_limit,
                event_limit=event_limit,
            )
        if dashboard is None:
            raise KeyError(run_id)
        return dashboard

    async def get_run_analysis(self, run_id: str) -> dict[str, Any]:
        payload = await asyncio.to_thread(self.runtime.get_run_analysis, run_id)
        if payload is None:
            raise KeyError(run_id)
        return payload

    async def delete_run(self, run_id: str) -> None:
        await asyncio.to_thread(self.runtime.delete_run, run_id)

    async def reset_all(self) -> None:
        await asyncio.to_thread(self.runtime.mutation, "runs:resetAll", {})

    async def start_run(self, run_id: str) -> RunState:
        run = await self.get_run(run_id, full=False)
        if run.status != RunStatus.RUNNING:
            await asyncio.to_thread(self.runtime.start_run, run_id)
            run = run.model_copy(update={"status": RunStatus.RUNNING})
        await self._ensure_run_sandboxes(run)
        return await self.get_run(run_id, full=False)

    async def stop_run(self, run_id: str) -> RunState:
        run = await self.get_run(run_id, full=False)
        if run.status in {"stopping", "finished", "failed"}:
            return run
        try:
            await asyncio.to_thread(
                self.runtime.request_stop,
                run_id,
                grace_seconds=run.config.soft_kill_grace_seconds,
            )
        except Exception:
            refreshed = await self.get_run(run_id, full=False)
            if refreshed.status in {"stopping", "finished", "failed"}:
                return refreshed
            raise
        self._spawn_terminator_background(run)
        return await self.get_run(run_id, full=False)

    async def leaderboard(self, run_id: str) -> dict[str, object]:
        dashboard = await asyncio.to_thread(self.runtime.get_run_dashboard, run_id)
        if dashboard is None:
            raise KeyError(run_id)
        state = dashboard["run"]["state"]
        bots = sorted(
            state["bots"].values(),
            key=lambda bot: float(bot.get("rating_score", 0.0)),
            reverse=True,
        )
        agents = []
        for agent in state["agents"].values():
            agents.append(
                {
                    "agent_id": agent["agent_id"],
                    "best_bot_id": agent.get("best_bot_id"),
                    "best_rating_score": round(float(agent.get("best_rating_score", 0.0)), 3),
                    "best_rating_mu": round(float(agent.get("best_rating_mu", 0.0)), 3),
                    "best_rating_sigma": round(float(agent.get("best_rating_sigma", 0.0)), 3),
                    "best_elo": round(float(agent.get("best_elo", 0.0)), 3),
                }
            )
        agents.sort(key=lambda item: item["best_rating_score"], reverse=True)
        return {
            "agents": agents,
            "bots": [
                {
                    "bot_id": bot["bot_id"],
                    "agent_id": bot["agent_id"],
                    "rating_score": round(float(bot.get("rating_score", 0.0)), 3),
                    "rating_mu": round(float(bot.get("rating_mu", 0.0)), 3),
                    "rating_sigma": round(float(bot.get("rating_sigma", 0.0)), 3),
                    "elo": round(float(bot.get("elo", 0.0)), 3),
                    "active": bool(bot.get("active", False)),
                    "name": bot.get("name", ""),
                }
                for bot in bots
            ],
        }

    async def list_games(self, run_id: str) -> list[dict[str, object]]:
        dashboard = await asyncio.to_thread(self.runtime.get_run_dashboard, run_id)
        if dashboard is None:
            raise KeyError(run_id)
        games = list(dashboard["run"]["state"].get("games", {}).values())
        games.sort(key=lambda item: item.get("started_at", ""), reverse=True)
        return [
            {
                "game_id": game["game_id"],
                "status": game["status"],
                "winner_bot_id": game.get("winner_bot_id"),
                "reason": game.get("reason"),
                "table_size": int(game.get("table_size", 0)),
                "round_count": int(game.get("round_count", 0)),
            }
            for game in games
        ]

    async def list_comments(self, run_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.runtime.list_recent_messages, run_id, limit=limit)

    async def get_agent_conversation(self, run_id: str, agent_id: str, limit: int = 400):
        return await asyncio.to_thread(self.runtime.get_agent_conversation, run_id, agent_id, limit=limit)

    async def steer_agent(self, run_id: str, agent_id: str, text: str) -> dict[str, object]:
        await self.get_run(run_id)
        result = await asyncio.to_thread(
            self.runtime.create_agent_steer,
            run_id,
            agent_id=agent_id,
            text=text,
        )
        return dict(result or {})

    async def _spawn_tournament_sandbox(self, run: RunState) -> None:
        await spawn_tournament_sandbox(run.run_id, run.config, self.runtime)

    async def _spawn_agent_sandbox(self, run: RunState, agent_id: str) -> None:
        await spawn_agent_sandbox(run.run_id, run.config, self.runtime, agent_id=agent_id)

    async def _ensure_run_sandboxes(self, run: RunState) -> None:
        sandboxes = await asyncio.to_thread(self.runtime.list_sandboxes, run.run_id)
        active_statuses = {"running", "stopping"}
        tournament_present = any(
            str(sandbox.get("kind") or sandbox.get("role") or "") == "tournament"
            and str(sandbox.get("status") or "") in active_statuses
            for sandbox in sandboxes
        )
        if not tournament_present:
            await self._spawn_tournament_sandbox(run)
        active_agents = {
            str(sandbox.get("agentId") or sandbox.get("agent_id") or "")
            for sandbox in sandboxes
            if str(sandbox.get("kind") or sandbox.get("role") or "") == "agent"
            and str(sandbox.get("status") or "") in active_statuses
        }
        configured_agent_ids = [agent.agent_id for agent in run.config.agents]
        missing = [agent_id for agent_id in configured_agent_ids if agent_id not in active_agents]
        if missing:
            await asyncio.gather(*[self._spawn_agent_sandbox(run, agent_id) for agent_id in missing])

    async def _spawn_terminator_sandbox(self, run: RunState) -> None:
        await spawn_terminator_sandbox(run.run_id, grace_seconds=run.config.soft_kill_grace_seconds)

    def _spawn_terminator_background(self, run: RunState) -> None:
        task = asyncio.create_task(self._spawn_terminator_sandbox(run))
        def _consume_exception(result: asyncio.Task[None]) -> None:
            try:
                result.exception()
            except Exception:
                pass

        task.add_done_callback(_consume_exception)
