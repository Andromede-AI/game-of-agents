from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Callable

from fastapi import HTTPException, status
import modal

from game_of_agents.agents.base import AgentContext
from game_of_agents.agents.command import CommandAgentRunner
from game_of_agents.agents.mock import MockAgentRunner
from game_of_agents.comment_feed import CommentFeedService
from game_of_agents.convex_sync import build_convex_sync
from game_of_agents.distributed_manager import DistributedRunManager
from game_of_agents.events import CompositeEventSink, ConvexEventSink, JsonlEventSink
from game_of_agents.marketplace import MarketplaceService
from game_of_agents.modal_runtime import (
    CONTROLLER_ROLE,
    SANDBOX_ROLE_ENV,
    app_secret,
    data_volume,
    image,
    in_controller_sandbox,
    lookup_app,
    running_inside_modal,
)
from game_of_agents.models import (
    AgentSteerRequest,
    AgentRuntime,
    BotSubmissionRequest,
    ConversationTurn,
    ConversationTurnKind,
    EventRecord,
    Purchase,
    RunConfig,
    RunState,
    RunStatus,
    new_id,
)
from game_of_agents.model_resolver import resolve_agent_config
from game_of_agents.settlement import compute_marketplace_payouts
from game_of_agents.settings import settings
from game_of_agents.store import RunStore
from game_of_agents.tournament import TournamentService
from game_of_agents.workspaces import WorkspaceManager


class Orchestrator:
    def __init__(
        self,
        read_hook: Callable[[], None] | None = None,
        write_hook: Callable[[], None] | None = None,
    ) -> None:
        self.distributed = DistributedRunManager() if settings.convex_url else None
        self.convex_sync = build_convex_sync()
        save_hooks = [self.convex_sync.sync_run] if self.convex_sync else []
        self.store = RunStore(
            settings.data_dir / "runs",
            save_hooks=save_hooks,
            read_hook=read_hook,
            write_hook=write_hook,
        )
        event_sinks = [JsonlEventSink(settings.data_dir / "events")]
        if self.convex_sync:
            event_sinks.append(ConvexEventSink(self.convex_sync))
        self.events = CompositeEventSink(event_sinks)
        self.workspaces = WorkspaceManager(
            settings.data_dir / "workspaces",
            read_hook=read_hook,
            write_hook=write_hook,
        )
        self.tournament = TournamentService(self.store, self.events, self.workspaces)
        self.marketplace = MarketplaceService(self.store, self.events, self.workspaces)
        self.comments = CommentFeedService(self.store, self.events)
        self._runners = {
            AgentRuntime.MOCK: MockAgentRunner(),
            AgentRuntime.CODEX: CommandAgentRunner("codex", read_hook=read_hook, write_hook=write_hook),
            AgentRuntime.CLAUDE: CommandAgentRunner("claude", read_hook=read_hook, write_hook=write_hook),
            AgentRuntime.GEMINI: CommandAgentRunner("gemini", read_hook=read_hook, write_hook=write_hook),
            AgentRuntime.OPENCODE: CommandAgentRunner("opencode", read_hook=read_hook, write_hook=write_hook),
        }
        self._tasks: dict[str, asyncio.Task[RunState]] = {}

    async def create_run(self, config: RunConfig) -> RunState:
        if self.distributed is not None:
            return await self.distributed.create_run(config)
        config = config.model_copy(
            update={"agents": [resolve_agent_config(a) for a in config.agents]}
        )
        run = RunState(config=config)
        for agent_config in config.agents:
            run.agents[agent_config.agent_id] = self.workspaces.scaffold_agent(run, agent_config)
        await self.store.save_run(run)
        await self.events.emit(EventRecord(run_id=run.run_id, kind="run.created", payload=run.model_dump(mode="json")))
        return run

    def create_run_sync(self, config: RunConfig) -> RunState:
        return asyncio.run(self.create_run(config))

    async def list_runs(self) -> list[RunState]:
        if self.distributed is not None:
            return await self.distributed.list_runs()
        runs = await self.store.list_runs()
        return [await self._refresh_live_run_state(run) for run in runs]

    async def get_run(self, run_id: str) -> RunState:
        if self.distributed is not None:
            try:
                return await self.distributed.get_run(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return await self._refresh_live_run_state(run)

    async def delete_run(self, run_id: str) -> None:
        if self.distributed is not None:
            try:
                run = await self.distributed.get_run(run_id)
                if run.status == RunStatus.RUNNING:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot delete a running run")
                await self.distributed.delete_run(run_id)
                return
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        if run.status == RunStatus.RUNNING:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="cannot delete a running run")
        task = self._tasks.pop(run_id, None)
        if task and not task.done():
            task.cancel()
        await self.store.delete_run(run_id)

    async def reset(self) -> None:
        """Delete all runs, events, workspaces, and Convex data."""
        import shutil

        if self.distributed is not None:
            await self.distributed.reset_all()
            return

        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        # Clear local data
        runs_dir = settings.data_dir / "runs"
        events_dir = settings.data_dir / "events"
        workspaces_dir = settings.data_dir / "workspaces"
        for directory in (runs_dir, events_dir, workspaces_dir):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True, exist_ok=True)
        # Clear Convex data
        if self.convex_sync:
            await self.convex_sync.reset()

    async def start_run(self, run_id: str) -> RunState:
        if self.distributed is not None:
            try:
                return await self.distributed.start_run(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
        run = await self.get_run(run_id)
        if run.status == RunStatus.RUNNING:
            return run
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(tz=UTC)
        await self.store.save_run(run)
        await self._submit_bootstrap_bots(run)
        run = await self.store.get_run(run_id) or run
        if self._should_launch_controller_sandbox():
            try:
                return await self._launch_controller_sandbox(run)
            except Exception as exc:
                await self.mark_run_failed(run_id, str(exc))
                raise
        self._tasks[run_id] = asyncio.create_task(self.run_to_completion(run_id))
        return run

    async def stop_run(self, run_id: str) -> RunState:
        if self.distributed is not None:
            try:
                return await self.distributed.stop_run(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
        run = await self.store.update_run(
            run_id,
            lambda current: self._set_run_status(current, RunStatus.STOPPING),
        )
        await self._reload_controller_volumes(run)
        return run

    async def steer_agent(self, run_id: str, agent_id: str, request: AgentSteerRequest) -> dict[str, object]:
        if self.distributed is not None:
            try:
                return await self.distributed.steer_agent(run_id, agent_id, request.text)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="agent steering is unsupported in local mode")

    async def run_iterations(self, run_id: str, iterations: int) -> RunState:
        if self.distributed is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="drain is unsupported in distributed mode")
        run = await self.get_run(run_id)
        if run.started_at is None:
            run.started_at = datetime.now(tz=UTC)
            run.status = RunStatus.RUNNING
            await self.store.save_run(run)
            await self._submit_bootstrap_bots(run)
            run = await self.store.get_run(run_id) or run
        warning_sent = False
        deadline = run.started_at + timedelta(minutes=run.config.duration_minutes)
        match_task = asyncio.create_task(self._run_match_loop(run_id, deadline))
        comment_task = asyncio.create_task(self._run_comment_loop(run_id, deadline))
        try:
            for _ in range(iterations):
                run = await self.get_run(run_id)
                if run.status in {RunStatus.STOPPING, RunStatus.FINISHED, RunStatus.FAILED}:
                    break
                minutes_left = max(0.0, (deadline - datetime.now(tz=UTC)).total_seconds() / 60)
                if not warning_sent and minutes_left <= run.config.last_warning_minutes:
                    await self._broadcast(run, minutes_left, True)
                    warning_sent = True
                await self._agent_steps(run, minutes_left)
                run = await self.get_run(run_id)
                await self.tournament.fill_match_queue(run_id, run.config.concurrent_matches)
                if datetime.now(tz=UTC) >= deadline:
                    break
        finally:
            await self._request_stop(run_id)
            await match_task
            await comment_task
        return await self.finalize_run(run_id)

    def run_once_sync(self, run_id: str) -> None:
        asyncio.run(self.run_iterations(run_id, 6))

    async def run_to_completion(self, run_id: str) -> RunState:
        try:
            await self._run_loop(run_id)
            return await self.get_run(run_id)
        except Exception as exc:
            await self.mark_run_failed(run_id, str(exc))
            raise

    async def _run_loop(self, run_id: str) -> None:
        run = await self.get_run(run_id)
        deadline = (run.started_at or datetime.now(tz=UTC)) + timedelta(minutes=run.config.duration_minutes)
        warning_sent = False
        match_task = asyncio.create_task(self._run_match_loop(run_id, deadline))
        comment_task = asyncio.create_task(self._run_comment_loop(run_id, deadline))
        try:
            while True:
                run = await self.get_run(run_id)
                if in_controller_sandbox():
                    now = datetime.now(tz=UTC)
                    last_seen = run.controller_last_seen_at
                    if last_seen is None or (now - last_seen).total_seconds() >= 5:
                        run = await self._touch_controller(run_id, now=now)
                if run.status == RunStatus.STOPPING:
                    break
                minutes_left = max(0.0, (deadline - datetime.now(tz=UTC)).total_seconds() / 60)
                if not warning_sent and minutes_left <= run.config.last_warning_minutes:
                    await self._broadcast(run, minutes_left, True)
                    warning_sent = True
                await self._agent_steps(run, minutes_left)
                run = await self.get_run(run_id)
                await self.tournament.fill_match_queue(run_id, run.config.concurrent_matches)
                if datetime.now(tz=UTC) >= deadline:
                    break
                await asyncio.sleep(0.1)
        finally:
            await self._request_stop(run_id)
            await match_task
            await comment_task
        await self.finalize_run(run_id)

    async def _run_match_loop(self, run_id: str, deadline: datetime) -> None:
        while True:
            run = await self.get_run(run_id)
            if run.status == RunStatus.STOPPING or datetime.now(tz=UTC) >= deadline:
                break
            await self.tournament.fill_match_queue(run_id, run.config.concurrent_matches)
            await asyncio.sleep(0.1)
        await self.tournament.wait_for_matches(run_id)

    async def _run_comment_loop(self, run_id: str, deadline: datetime) -> None:
        run = await self.store.get_run(run_id)
        if run is None or not run.config.comment_feed.enabled:
            return
        next_fire = datetime.now(tz=UTC) + timedelta(seconds=run.config.comment_feed.cadence_seconds)
        while True:
            run = await self.get_run(run_id)
            now = datetime.now(tz=UTC)
            if run.status == RunStatus.STOPPING:
                return
            if now >= next_fire:
                try:
                    messages = await self.comments.maybe_run_sidecars(run)
                    if messages:
                        await self.events.emit(
                            EventRecord(
                                run_id=run_id,
                                kind="comment.sidecars.ran",
                                payload={"count": len(messages)},
                            )
                        )
                except Exception as exc:
                    await self.events.emit(
                        EventRecord(
                            run_id=run_id,
                            kind="comment.sidecars.failed",
                            payload={"error": str(exc)},
                        )
                    )
                next_fire = now + timedelta(seconds=run.config.comment_feed.cadence_seconds)
            if now >= deadline:
                return
            await asyncio.sleep(0.5)

    async def _agent_steps(self, run: RunState, minutes_left: float) -> None:
        leaderboard = await self.tournament.leaderboard(run.run_id)
        ranking = {entry["agent_id"]: index + 1 for index, entry in enumerate(leaderboard["agents"])}
        await asyncio.gather(
            *[
                self._agent_step(run.run_id, agent.agent_id, minutes_left, ranking.get(agent.agent_id, len(run.agents)))
                for agent in run.agents.values()
            ]
        )

    async def _agent_step(self, run_id: str, agent_id: str, minutes_left: float, rank: int) -> None:
        started_at = datetime.now(tz=UTC)
        current = await self._patch_agent(
            run_id,
            agent_id,
            lambda agent: self._mark_agent_running(agent, started_at),
        )
        active_agent = current.agents[agent_id]

        context = AgentContext(
            run=current,
            agent=active_agent,
            minutes_left=minutes_left,
            best_elo=active_agent.best_elo,
            rank=rank,
            step_id=new_id("step"),
            report_event=lambda kind, payload: self.events.emit(
                EventRecord(run_id=run_id, kind=kind, payload=payload)
            ),
            update_agent_state=lambda updates: self._update_agent_state(run_id, agent_id, updates),
        )
        runner = self._runners[active_agent.runtime]
        prompt = runner.step_prompt(context) or runner.last_prompt(context)
        if prompt:
            await self._record_agent_turn(
                run_id,
                agent_id,
                ConversationTurnKind.PROMPT,
                prompt,
                step_id=context.step_id,
            )
            await self.events.emit(
                EventRecord(
                    run_id=run_id,
                    kind="agent.prompt",
                    payload={"agent_id": agent_id, "step_id": context.step_id, "message": prompt},
                )
            )
        try:
            submission = await runner.step(context)
        except Exception as exc:
            await self._record_agent_turn(
                run_id,
                agent_id,
                ConversationTurnKind.ERROR,
                str(exc),
                step_id=context.step_id,
            )
            await self._patch_agent(
                run_id,
                agent_id,
                lambda agent: self._mark_agent_failed(agent, str(exc), datetime.now(tz=UTC)),
            )
            await self.events.emit(
                EventRecord(
                    run_id=run_id,
                    kind="agent.failed",
                    payload={"agent_id": agent_id, "step_id": context.step_id, "error": str(exc)},
                )
            )
            return
        if submission is not None:
            await self.tournament.submit_bot(run_id, submission)
        full_output = runner.last_full_output(context)
        step_summary = runner.last_summary(context) or (
            f"Submitted {submission.name}." if submission is not None else "Completed without a new bot revision."
        )
        await self._record_agent_turn(
            run_id,
            agent_id,
            ConversationTurnKind.RESPONSE,
            full_output or step_summary,
            step_id=context.step_id,
        )
        await self._patch_agent(
            run_id,
            agent_id,
            lambda agent: self._mark_agent_idle(agent, step_summary, datetime.now(tz=UTC)),
        )
        await self.events.emit(
            EventRecord(
                run_id=run_id,
                kind="agent.step",
                payload={
                    "agent_id": agent_id,
                    "step_id": context.step_id,
                    "message": step_summary,
                    "submission_name": submission.name if submission is not None else None,
                },
            )
        )
        if full_output:
            await self.events.emit(
                EventRecord(
                    run_id=run_id,
                    kind="agent.output",
                    payload={"agent_id": agent_id, "step_id": context.step_id, "output": full_output},
                )
            )

    async def _broadcast(self, run: RunState, minutes_left: float, is_warning: bool) -> None:
        leaderboard = await self.tournament.leaderboard(run.run_id)
        ranking = {entry["agent_id"]: index + 1 for index, entry in enumerate(leaderboard["agents"])}
        for agent in run.agents.values():
            context = AgentContext(
                run=run,
                agent=agent,
                minutes_left=minutes_left,
                best_elo=agent.best_elo,
                rank=ranking.get(agent.agent_id, len(run.agents)),
                last_warning=is_warning,
            )
            message = await self._runners[agent.runtime].continue_message(context)
            await self._record_agent_turn(
                run.run_id,
                agent.agent_id,
                ConversationTurnKind.WARNING if is_warning else ConversationTurnKind.PROMPT,
                message,
                step_id=None,
            )
            await self.events.emit(
                EventRecord(
                    run_id=run.run_id,
                    kind="agent.warning" if is_warning else "agent.message",
                    payload={"agent_id": agent.agent_id, "message": message},
                )
            )

    async def _record_agent_turn(
        self,
        run_id: str,
        agent_id: str,
        kind: ConversationTurnKind,
        text: str | None,
        *,
        step_id: str | None = None,
    ) -> None:
        if not text:
            return
        await self.comments.record_turn(
            run_id,
            ConversationTurn(
                run_id=run_id,
                agent_id=agent_id,
                kind=kind,
                text=text,
                step_id=step_id,
            ),
        )

    async def _submit_bootstrap_bots(self, run: RunState | str) -> None:
        current = run if isinstance(run, RunState) else await self.store.get_run(run)
        if current is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        run_id = current.run_id
        for agent in current.agents.values():
            if agent.best_bot_id is not None:
                continue
            await self.tournament.submit_bot(
                run_id,
                request=BotSubmissionRequest(
                    agent_id=agent.agent_id,
                    name=f"{agent.agent_id}-bot-0",
                    description="Initial scaffold bot.",
                    entrypoint="WorkspaceBot",
                    module_path="bot.py",
                ),
            )

    async def finalize_run(self, run_id: str) -> RunState:
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        if run.status == RunStatus.FINISHED:
            return run
        if run.config.comment_feed.enabled:
            try:
                messages = await self.comments.maybe_run_sidecars(run)
                if messages:
                    await self.events.emit(
                        EventRecord(
                            run_id=run_id,
                            kind="comment.sidecars.ran",
                            payload={"count": len(messages), "phase": "finalize"},
                        )
                    )
            except Exception as exc:
                await self.events.emit(
                    EventRecord(
                        run_id=run_id,
                        kind="comment.sidecars.failed",
                        payload={"error": str(exc), "phase": "finalize"},
                    )
                )
            run = await self.get_run(run_id)
        await self._shutdown_runners(run)
        await self.tournament.shutdown_run(run_id)
        base_scores: dict[str, float] = {}
        for agent in run.agents.values():
            best_bot = (
                run.bots.get(agent.best_bot_id) if agent.best_bot_id is not None else None
            )
            base_scores[agent.agent_id] = best_bot.elo if best_bot else 1000.0
        payouts = compute_marketplace_payouts(base_scores, run.purchases.values(), run.config.settlement_mode)
        run.final_scores = base_scores
        run.payouts = payouts
        run.status = RunStatus.FINISHED
        run.finished_at = datetime.now(tz=UTC)
        run.controller_status = "finished"
        run.controller_last_seen_at = datetime.now(tz=UTC)
        await self.store.save_run(run)
        await self.events.emit(
            EventRecord(
                run_id=run_id,
                kind="run.finished",
                payload={"final_scores": base_scores, "payouts": payouts},
            )
        )
        return run

    async def _request_stop(self, run_id: str) -> None:
        run = await self.get_run(run_id)
        if run.status in {RunStatus.FINISHED, RunStatus.FAILED, RunStatus.STOPPING}:
            return
        run.status = RunStatus.STOPPING
        await self.store.save_run(run)

    async def mark_run_failed(self, run_id: str, error: str) -> RunState:
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        if run.status == RunStatus.FINISHED:
            return run
        await self._shutdown_runners(run)
        await self.tournament.shutdown_run(run_id)
        for agent in run.agents.values():
            if agent.status == "running":
                agent.status = "failed"
                agent.current_step_started_at = None
                agent.last_message = agent.last_message or error
        run.status = RunStatus.FAILED
        run.finished_at = datetime.now(tz=UTC)
        run.last_error = error
        run.controller_status = "failed"
        run.controller_last_seen_at = datetime.now(tz=UTC)
        await self.store.save_run(run)
        await self.events.emit(
            EventRecord(run_id=run_id, kind="run.failed", payload={"error": error})
        )
        return run

    async def _update_agent_state(self, run_id: str, agent_id: str, updates: dict[str, object]) -> None:
        await self._patch_agent(
            run_id,
            agent_id,
            lambda agent: self._apply_agent_updates(agent, updates),
        )

    async def _shutdown_runners(self, run: RunState) -> None:
        seen: set[int] = set()
        for agent in run.agents.values():
            runner = self._runners[agent.runtime]
            if id(runner) in seen:
                continue
            seen.add(id(runner))
            await runner.shutdown_run(run)

    async def _refresh_live_run_state(self, run: RunState) -> RunState:
        if (
            in_controller_sandbox()
            or not running_inside_modal()
            or run.status not in {RunStatus.RUNNING, RunStatus.STOPPING}
        ):
            return run
        try:
            sandbox = await self._controller_sandbox_handle(run)
            exit_code = await sandbox.poll.aio()
        except Exception as exc:
            last_seen = run.controller_last_seen_at
            if last_seen is not None and (datetime.now(tz=UTC) - last_seen).total_seconds() < 120:
                return run
            return await self.mark_run_failed(
                run.run_id,
                f"controller sandbox lookup failed: {exc}",
            )
        if exit_code is None:
            if run.controller_status != "running" or run.controller_sandbox_id != sandbox.object_id:
                run.controller_sandbox_id = sandbox.object_id
                run.controller_status = "running"
                run.controller_last_seen_at = datetime.now(tz=UTC)
                await self.store.save_run(run)
            return run
        if run.status == RunStatus.STOPPING:
            return await self.finalize_run(run.run_id)
        if run.status == RunStatus.RUNNING:
            return await self.mark_run_failed(
                run.run_id,
                f"controller sandbox exited unexpectedly with code {exit_code}",
            )
        return run

    def _should_launch_controller_sandbox(self) -> bool:
        return running_inside_modal() and not in_controller_sandbox()

    async def _launch_controller_sandbox(self, run: RunState) -> RunState:
        app = await lookup_app()
        sandbox = await modal.Sandbox.create.aio(
            "python",
            "-m",
            "game_of_agents.sandbox_controller",
            run.run_id,
            app=app,
            name=self._controller_sandbox_name(run.run_id),
            image=image,
            secrets=[app_secret],
            volumes={"/goa_data": data_volume},
            timeout=max(900, run.config.duration_minutes * 60 + 1800),
            idle_timeout=max(600, run.config.duration_minutes * 60 + 600),
            workdir="/root",
            cpu=run.config.controller_cpu or run.config.runner_cpu or min(8, max(2, run.config.concurrent_matches)),
            memory=2048,
            env={
                SANDBOX_ROLE_ENV: CONTROLLER_ROLE,
                "GOA_RUN_ID": run.run_id,
                "DATA_DIR": "/goa_data",
            },
        )
        run = await self._touch_controller(
            run.run_id,
            sandbox_id=sandbox.object_id,
            now=datetime.now(tz=UTC),
        )
        await self.events.emit(
            EventRecord(
                run_id=run.run_id,
                kind="run.controller.started",
                payload={"sandbox_id": sandbox.object_id},
            )
        )
        return run

    async def _controller_sandbox_handle(self, run: RunState) -> modal.Sandbox:
        if run.controller_sandbox_id:
            try:
                return await modal.Sandbox.from_id.aio(run.controller_sandbox_id)
            except Exception:
                pass
        return await modal.Sandbox.from_name.aio("game-of-agents", self._controller_sandbox_name(run.run_id))

    async def _reload_controller_volumes(self, run: RunState) -> None:
        if not running_inside_modal() or in_controller_sandbox():
            return
        if not run.controller_sandbox_id:
            return
        try:
            sandbox = await self._controller_sandbox_handle(run)
            await sandbox.reload_volumes.aio()
        except Exception:
            return

    def _controller_sandbox_name(self, run_id: str) -> str:
        return f"goa-controller-{run_id}"

    async def _touch_controller(
        self,
        run_id: str,
        *,
        sandbox_id: str | None = None,
        now: datetime | None = None,
    ) -> RunState:
        timestamp = now or datetime.now(tz=UTC)
        return await self.store.update_run(
            run_id,
            lambda current: self._set_controller_presence(current, sandbox_id, timestamp),
        )

    def _set_controller_presence(
        self,
        run: RunState,
        sandbox_id: str | None,
        now: datetime,
    ) -> RunState:
        if sandbox_id is not None:
            run.controller_sandbox_id = sandbox_id
        run.controller_status = "running"
        run.controller_last_seen_at = now
        return run

    def _set_run_status(self, run: RunState, status: RunStatus) -> RunState:
        run.status = status
        return run

    async def _patch_agent(
        self,
        run_id: str,
        agent_id: str,
        mutator,
    ) -> RunState:
        return await self.store.update_run(
            run_id,
            lambda current: self._mutate_agent(current, agent_id, mutator),
        )

    def _mutate_agent(self, run: RunState, agent_id: str, mutator) -> RunState:
        agent = run.agents[agent_id]
        mutator(agent)
        run.agents[agent_id] = agent
        return run

    def _mark_agent_running(self, agent, started_at: datetime) -> None:
        agent.status = "running"
        agent.current_step_started_at = started_at
        agent.last_activity_at = started_at

    def _mark_agent_failed(self, agent, message: str, timestamp: datetime) -> None:
        agent.status = "failed"
        agent.last_message = message
        agent.current_step_started_at = None
        agent.last_activity_at = timestamp

    def _mark_agent_idle(self, agent, message: str, timestamp: datetime) -> None:
        agent.status = "idle"
        agent.last_message = message
        agent.current_step_started_at = None
        agent.last_activity_at = timestamp

    def _apply_agent_updates(self, agent, updates: dict[str, object]) -> None:
        for key, value in updates.items():
            setattr(agent, key, value)
