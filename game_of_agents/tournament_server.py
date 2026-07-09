from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import combinations
from math import fabs
import multiprocessing as mp
import os
from pathlib import Path
from typing import Any

from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.logging import configure_logging
from game_of_agents.models import (
    AgentState,
    BotSubmission,
    GameStatus,
    MatchParticipantResult,
    MatchResult,
    RunConfig,
)
from game_of_agents.rating import build_openskill_model, create_rating, update_bot_rating
from game_of_agents.sandbox_spawner import spawn_agent_sandbox, terminate_sandbox
from game_of_agents.settlement import compute_marketplace_payouts
from game_of_agents.settings import settings
from game_of_agents.tournament import _execute_match_job


MAX_AGENT_RESPAWNS = 3


class TournamentServer:
    def __init__(self, run_id: str) -> None:
        if not settings.convex_url:
            raise RuntimeError("CONVEX_URL is required")
        self.run_id = run_id
        self.runtime = ConvexRuntimeClient(
            settings.convex_url,
            site_url=settings.convex_site_url,
            auth_token=settings.convex_sync_token,
        )
        self.sandbox_id = os.environ.get("MODAL_SANDBOX_ID", "")
        self.bundle_root = Path("/tmp/goa-bundles") / run_id
        self.bundle_root.mkdir(parents=True, exist_ok=True)
        self._executor: Executor | None = None
        self._match_tasks: dict[asyncio.Task[MatchResult], list[str]] = {}
        self._inflight_counts: Counter[str] = Counter()
        self._completed_pair_counts: Counter[frozenset[str]] = Counter()
        self._completed_match_counts: Counter[str] = Counter()
        self._bot_cache: dict[str, tuple[dict[str, Any], Path]] = {}
        self._bots: dict[str, BotSubmission] = {}
        self._agents: dict[str, AgentState] = {}
        self._peak_live_matches = 0
        self._last_sandbox_watchdog_at: datetime | None = None
        self._agent_respawn_counts: Counter[str] = Counter()
        self._last_agent_respawn_at: dict[str, datetime] = {}

    async def run(self) -> None:
        control = await self._control()
        state = await self._tournament_state()
        run_config = RunConfig.model_validate(control["config"])
        self._hydrate_state(state)
        started_at = _from_millis(control["startedAt"])
        await self._announce_sandbox(run_config)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(run_config))
        try:
            await self._loop(run_config, started_at)
        except Exception as exc:
            await self._cancel_matches()
            try:
                await asyncio.to_thread(
                    self.runtime.fail_run,
                    self.run_id,
                    f"tournament sandbox crashed: {type(exc).__name__}: {exc}",
                )
                await asyncio.to_thread(
                    self.runtime.finish_sandbox,
                    self.run_id,
                    sandbox_id=self.sandbox_id,
                    status="failed",
                    error=str(exc),
                )
            except Exception:
                pass
            raise
        finally:
            heartbeat_task.cancel()
            await self._shutdown_executor()

    async def _announce_sandbox(self, run_config: RunConfig) -> None:
        ttl = max(30, int(max(1.0, run_config.tournament_poll_seconds) * 10))
        metadata = {
            "current_live_matches": len(self._match_tasks),
            "peak_live_matches": self._peak_live_matches,
            "worker_parallelism": self._parallelism(run_config),
        }
        try:
            await asyncio.to_thread(
                self.runtime.heartbeat_sandbox,
                self.run_id,
                sandbox_id=self.sandbox_id,
                status="running",
                metadata_patch=metadata,
                heartbeat_ttl_seconds=ttl,
            )
            return
        except Exception:
            pass
        await asyncio.to_thread(
            self.runtime.register_sandbox,
            self.run_id,
            role="tournament",
            sandbox_id=self.sandbox_id,
            status="running",
            metadata=metadata,
            heartbeat_ttl_seconds=ttl,
        )

    async def _loop(self, run_config: RunConfig, started_at: datetime) -> None:
        deadline = started_at + timedelta(minutes=run_config.duration_minutes)
        convergence_deadline = deadline + timedelta(minutes=run_config.convergence_tail_minutes)
        frozen = False
        while True:
            await self._drain_finished_tasks(run_config)
            control = await self._control()
            status = str(control["status"])
            now = datetime.now(tz=UTC)
            if status == "failed":
                await self._cancel_matches()
                await self._finish_run(run_config, failed=True)
                return
            if status == "stopping":
                await self._cancel_matches()
                await self._finish_run(run_config, failed=False)
                return
            if status == "finished":
                await asyncio.to_thread(
                    self.runtime.finish_sandbox,
                    self.run_id,
                    sandbox_id=self.sandbox_id,
                    status="finished",
                    error=None,
                )
                return
            if not frozen and now >= deadline:
                frozen = True
                await asyncio.to_thread(self.runtime.set_read_only, self.run_id)
            if not frozen:
                await self._admit_pending_submissions(run_config)
                await self._watch_stale_agents(run_config, deadline)
            await self._fill_match_queue(run_config)
            if now >= convergence_deadline:
                await self._wait_for_matches(run_config)
                await self._finish_run(run_config, failed=False)
                return
            await asyncio.sleep(run_config.tournament_poll_seconds)

    async def _admit_pending_submissions(self, run_config: RunConfig) -> None:
        submissions = await asyncio.to_thread(self.runtime.list_pending_submissions, self.run_id)
        if not submissions:
            return
        for submission in submissions:
            bundle_root = await self._ensure_bundle(
                cache_key=str(submission["submissionId"]),
                storage_id=str(submission["bundleStorageId"]),
            )
            module_path = bundle_root / self._bundle_module_path(submission)
            bot = BotSubmission(
                agent_id=str(submission["agentId"]),
                name=str(submission["name"]),
                description=str(submission.get("description") or ""),
                entrypoint=str(submission["entrypoint"]),
                module_path=str(module_path),
                bundle_storage_id=str(submission["bundleStorageId"]),
                bundle_bytes=int(submission.get("bundleBytes") or submission.get("bundle_bytes") or 0),
                file_paths=[str(path) for path in submission.get("filePaths") or submission.get("file_paths") or []],
                created_at=_from_millis(submission.get("createdAt")),
                artifacts=[],
            )
            self._bots[bot.bot_id] = bot
            evicted_bot_ids = self._enforce_cap(
                run_config,
                self._agents,
                self._bots,
                bot.agent_id,
                max_evictions=1,
            )
            evicted_bot_id = evicted_bot_ids[-1] if evicted_bot_ids else None
            await asyncio.to_thread(
                self.runtime.activate_submission,
                self.run_id,
                submission_id=str(submission["submissionId"]),
                bot=bot,
                evicted_bot_id=evicted_bot_id,
            )
            self._bot_cache[bot.bot_id] = (submission, bundle_root)
            for evicted_id in evicted_bot_ids:
                self._bot_cache.pop(evicted_id, None)

    async def _fill_match_queue(self, run_config: RunConfig) -> None:
        target = self._parallelism(run_config)
        open_slots = max(0, min(run_config.concurrent_matches, target) - len(self._match_tasks))
        if open_slots == 0:
            return
        bots = [bot.model_copy(deep=True) for bot in self._bots.values() if bot.active]
        if len(bots) < run_config.game.players_per_match:
            return
        tables = self._pick_tables(bots, open_slots, run_config)
        await self._prime_bundle_cache(tables)
        for table in tables:
            scheduled = [bot.model_copy(deep=True) for bot in table]
            bot_ids = [bot.bot_id for bot in scheduled]
            self._inflight_counts.update(bot_ids)
            task = asyncio.create_task(self._run_match(scheduled, run_config))
            self._match_tasks[task] = bot_ids
        self._peak_live_matches = max(self._peak_live_matches, len(self._match_tasks))

    async def _run_match(self, table: list[BotSubmission], run_config: RunConfig) -> MatchResult:
        loop = asyncio.get_running_loop()
        workspaces: list[str] = []
        bot_payloads: list[dict[str, Any]] = []
        for bot in table:
            submission, bundle_root = await self._bundle_for_bot(bot.bot_id)
            bot.module_path = str(bundle_root / self._bundle_module_path(submission))
            bot_payloads.append(bot.model_dump(mode="json"))
            workspaces.append(str(bundle_root))
        match_payload = await loop.run_in_executor(
            self._executor_for(run_config),
            _execute_match_job,
            self.run_id,
            bot_payloads,
            workspaces,
            run_config.model_dump(mode="json"),
            run_config.capture_actions,
        )
        return MatchResult.model_validate(match_payload)

    def _apply_ratings(
        self,
        match: MatchResult,
        run_config: RunConfig,
    ) -> tuple[list[BotSubmission], list[AgentState]]:
        model = build_openskill_model(run_config.rating)
        teams = []
        placements = []
        touched_agents: set[str] = set()
        touched_bots: list[BotSubmission] = []
        ordered = sorted(match.participants, key=lambda item: item.placement)
        for participant in ordered:
            bot = self._bots[participant.bot_id]
            teams.append([create_rating(model, bot.rating_mu, bot.rating_sigma)])
            placements.append(participant.placement - 1)
        updated = model.rate(teams, ranks=placements)
        for participant, rating in zip(ordered, updated):
            bot = self._bots[participant.bot_id]
            bot.matches_played += 1
            update_bot_rating(bot, run_config.rating, rating[0])
            self._bots[bot.bot_id] = bot
            touched_bots.append(bot)
            touched_agents.add(bot.agent_id)
        updated_agents: list[AgentState] = []
        for agent_id in touched_agents:
            evicted_bot_ids = self._enforce_cap(run_config, self._agents, self._bots, agent_id)
            for evicted_id in evicted_bot_ids:
                touched_bots.append(self._bots[evicted_id])
                self._bot_cache.pop(evicted_id, None)
            agent = self._agents[agent_id]
            updated_agents.append(agent)
        return touched_bots, updated_agents

    def _pick_tables(
        self,
        bots: list[BotSubmission],
        limit: int,
        run_config: RunConfig,
    ) -> list[list[BotSubmission]]:
        bots = sorted(bots, key=lambda bot: bot.rating_score, reverse=True)
        pair_counts = Counter(self._completed_pair_counts)
        projected = Counter(self._inflight_counts)
        tables: list[list[BotSubmission]] = []
        for _ in range(limit):
            table: list[BotSubmission] | None = None
            for seed in self._seed_candidates(bots, projected, run_config):
                candidate = self._build_table(seed, bots, projected, pair_counts, run_config)
                if len(candidate) == run_config.game.players_per_match:
                    table = candidate
                    break
            if table is None:
                break
            tables.append(table)
            projected.update(bot.bot_id for bot in table)
            for left, right in combinations([bot.bot_id for bot in table], 2):
                pair_counts[frozenset((left, right))] += 1
        return tables

    def _seed_candidates(
        self,
        bots: list[BotSubmission],
        inflight: Counter[str],
        run_config: RunConfig,
    ) -> list[BotSubmission]:
        max_per_bot = run_config.game.max_concurrent_matches_per_bot
        eligible = [
            bot
            for bot in bots
            if max_per_bot is None or inflight[bot.bot_id] < max_per_bot
        ]
        return sorted(
            eligible,
            key=lambda bot: (
                self._completed_match_counts[bot.bot_id] + inflight[bot.bot_id],
                inflight[bot.bot_id],
                -bot.created_at.timestamp(),
                -bot.rating_score,
                bot.bot_id,
            ),
        )

    def _build_table(
        self,
        seed: BotSubmission,
        bots: list[BotSubmission],
        inflight: Counter[str],
        pair_counts: Counter[frozenset[str]],
        run_config: RunConfig,
    ) -> list[BotSubmission]:
        table = [seed]
        used_agents = {seed.agent_id}
        spread = run_config.rating.matchmaking_spread
        max_per_bot = run_config.game.max_concurrent_matches_per_bot
        while len(table) < run_config.game.players_per_match:
            candidates = []
            for candidate in bots:
                if any(existing.bot_id == candidate.bot_id for existing in table):
                    continue
                if max_per_bot is not None and inflight[candidate.bot_id] >= max_per_bot:
                    continue
                if not run_config.game.allow_same_agent_table and candidate.agent_id in used_agents:
                    continue
                if len(table) > 1 and fabs(candidate.rating_score - seed.rating_score) > spread:
                    continue
                repeat_score = sum(pair_counts[frozenset((candidate.bot_id, existing.bot_id))] for existing in table)
                candidates.append(
                    (
                        repeat_score,
                        self._completed_match_counts[candidate.bot_id] + inflight[candidate.bot_id],
                        fabs(candidate.rating_score - seed.rating_score),
                        -candidate.created_at.timestamp(),
                        candidate,
                    )
                )
            if not candidates:
                break
            _, _, _, _, chosen = min(candidates, key=lambda item: (item[0], item[1], item[2], item[3], item[4].bot_id))
            table.append(chosen)
            used_agents.add(chosen.agent_id)
        return table

    def _enforce_cap(
        self,
        run_config: RunConfig,
        agents: dict[str, AgentState],
        bots: dict[str, BotSubmission],
        agent_id: str,
        *,
        max_evictions: int | None = None,
    ) -> list[str]:
        active = [bot for bot in bots.values() if bot.agent_id == agent_id and bot.active]
        evicted_bot_ids: list[str] = []
        while len(active) > run_config.max_active_bots_per_agent:
            if max_evictions is not None and len(evicted_bot_ids) >= max_evictions:
                break
            eligible = [bot for bot in active if bot.matches_played > 0]
            if not eligible:
                break
            worst = min(eligible, key=lambda bot: (bot.rating_score, bot.elo, bot.created_at, bot.bot_id))
            worst.active = False
            bots[worst.bot_id] = worst
            evicted_bot_ids.append(worst.bot_id)
            active = [bot for bot in active if bot.bot_id != worst.bot_id]
        self._refresh_agent_best(agents, bots, agent_id)
        return evicted_bot_ids

    def _refresh_agent_best(
        self,
        agents: dict[str, AgentState],
        bots: dict[str, BotSubmission],
        agent_id: str,
    ) -> None:
        agent = agents[agent_id]
        active = sorted(
            [bot for bot in bots.values() if bot.agent_id == agent_id and bot.active],
            key=lambda item: item.rating_score,
            reverse=True,
        )
        if not active:
            agent.best_bot_id = None
            agents[agent_id] = agent
            return
        best = active[0]
        agent.best_bot_id = best.bot_id
        agent.best_rating_mu = best.rating_mu
        agent.best_rating_sigma = best.rating_sigma
        agent.best_rating_score = best.rating_score
        agent.best_elo = best.elo
        agents[agent_id] = agent

    async def _bundle_for_bot(self, bot_id: str) -> tuple[dict[str, Any], Path]:
        if bot_id in self._bot_cache:
            return self._bot_cache[bot_id]
        bot = self._bots[bot_id]
        storage_id = bot.bundle_storage_id
        if not storage_id:
            raise RuntimeError(f"bot {bot_id} missing bundle storage id")
        root = await self._ensure_bundle(cache_key=bot_id, storage_id=str(storage_id))
        payload = {
            "submissionId": bot_id,
            "bundleStorageId": storage_id,
            "modulePath": bot.module_path,
            "entrypoint": bot.entrypoint,
            "filePaths": bot.file_paths,
        }
        self._bot_cache[bot_id] = (payload, root)
        return payload, root

    async def _prime_bundle_cache(self, tables: list[list[BotSubmission]]) -> None:
        pending: list[str] = []
        seen: set[str] = set()
        for table in tables:
            for bot in table:
                if bot.bot_id in seen or bot.bot_id in self._bot_cache:
                    continue
                seen.add(bot.bot_id)
                pending.append(bot.bot_id)
        if not pending:
            return
        semaphore = asyncio.Semaphore(min(32, len(pending)))

        async def ensure(bot_id: str) -> None:
            async with semaphore:
                await self._bundle_for_bot(bot_id)

        await asyncio.gather(*(ensure(bot_id) for bot_id in pending))

    async def _ensure_bundle(self, *, cache_key: str, storage_id: str) -> Path:
        path = self.bundle_root / cache_key
        if path.exists():
            return path
        await asyncio.to_thread(self.runtime.download_bundle, storage_id, path)
        return path

    def _bundle_module_path(self, payload: dict[str, Any]) -> Path:
        raw = str(payload.get("modulePath") or payload.get("module_path") or "")
        path = Path(raw)
        if not path.is_absolute():
            return path
        file_paths = payload.get("filePaths") or payload.get("file_paths") or []
        for candidate in file_paths:
            candidate_path = Path(str(candidate))
            if candidate_path.name == path.name:
                return candidate_path
        return Path(path.name)

    def _parallelism(self, run_config: RunConfig) -> int:
        return (
            run_config.match_worker_processes
            or run_config.match_worker_threads
            or min(os.cpu_count() or 1, run_config.concurrent_matches)
        )

    def _executor_for(self, run_config: RunConfig) -> Executor:
        if self._executor is not None:
            return self._executor
        workers = self._parallelism(run_config)
        if run_config.match_executor == "thread":
            self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"goa-match-{self.run_id[-4:]}")
        else:
            kwargs: dict[str, Any] = {"max_workers": workers}
            if os.name != "nt":
                kwargs["mp_context"] = mp.get_context("forkserver")
            self._executor = ProcessPoolExecutor(**kwargs)
        return self._executor

    async def _wait_for_matches(self, run_config: RunConfig) -> None:
        while self._match_tasks:
            tasks = list(self._match_tasks)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if done:
                await self._drain_finished_tasks(run_config)

    async def _cancel_matches(self) -> None:
        for task in list(self._match_tasks):
            task.cancel()
        self._match_tasks.clear()
        await self._shutdown_executor(cancel=True)

    async def _shutdown_executor(self, cancel: bool = False) -> None:
        executor = self._executor
        self._executor = None
        if executor is None:
            return
        await asyncio.to_thread(executor.shutdown, wait=not cancel, cancel_futures=cancel)

    async def _finish_run(self, run_config: RunConfig, *, failed: bool) -> None:
        final_scores: dict[str, float] = {}
        for agent_id, agent in self._agents.items():
            score = float(agent.best_rating_score)
            final_scores[agent_id] = score
        state = await self._tournament_state()
        payouts = compute_marketplace_payouts(final_scores, state.get("purchases", []), run_config.settlement_mode)
        if failed:
            await asyncio.to_thread(self.runtime.fail_run, self.run_id, "tournament sandbox failed")
        else:
            await asyncio.to_thread(self.runtime.complete_run, self.run_id, final_scores=final_scores, payouts=payouts)
        await asyncio.to_thread(
            self.runtime.finish_sandbox,
            self.run_id,
            sandbox_id=self.sandbox_id,
            status="finished",
            error=None,
        )

    async def _watch_stale_agents(self, run_config: RunConfig, deadline: datetime) -> None:
        now = datetime.now(tz=UTC)
        if now >= deadline:
            return
        interval_seconds = max(10.0, run_config.agent_poll_seconds * 10.0)
        if self._last_sandbox_watchdog_at and (now - self._last_sandbox_watchdog_at).total_seconds() < interval_seconds:
            return
        self._last_sandbox_watchdog_at = now
        sandboxes = await asyncio.to_thread(self.runtime.list_sandboxes, self.run_id)
        for agent_id, sandbox in self._stale_agent_candidates(sandboxes, run_config, now=now):
            await self._respawn_agent(agent_id, sandbox, run_config)

    def _stale_agent_candidates(
        self,
        sandboxes: list[dict[str, Any]],
        run_config: RunConfig,
        *,
        now: datetime,
    ) -> list[tuple[str, dict[str, Any]]]:
        latest_by_agent: dict[str, dict[str, Any]] = {}
        for sandbox in sandboxes:
            if str(sandbox.get("kind") or sandbox.get("role") or "") != "agent":
                continue
            agent_id = str(sandbox.get("agentId") or sandbox.get("agent_id") or "")
            if not agent_id or agent_id == "unknown":
                continue
            current = latest_by_agent.get(agent_id)
            if current is None:
                latest_by_agent[agent_id] = sandbox
                continue
            current_ts = int(current.get("lastHeartbeatAt") or current.get("registeredAt") or 0)
            sandbox_ts = int(sandbox.get("lastHeartbeatAt") or sandbox.get("registeredAt") or 0)
            if sandbox_ts >= current_ts:
                latest_by_agent[agent_id] = sandbox
        cooldown_seconds = max(30.0, run_config.agent_poll_seconds * 20.0)
        candidates: list[tuple[str, dict[str, Any]]] = []
        for agent_id, sandbox in latest_by_agent.items():
            if str(sandbox.get("status") or "") != "stale":
                continue
            if self._agent_respawn_counts[agent_id] >= MAX_AGENT_RESPAWNS:
                continue
            last_respawn = self._last_agent_respawn_at.get(agent_id)
            if last_respawn and (now - last_respawn).total_seconds() < cooldown_seconds:
                continue
            candidates.append((agent_id, sandbox))
        return candidates

    async def _respawn_agent(self, agent_id: str, sandbox: dict[str, Any], run_config: RunConfig) -> None:
        now = datetime.now(tz=UTC)
        old_sandbox_id = str(sandbox.get("sandboxId") or sandbox.get("sandbox_id") or "")
        self._last_agent_respawn_at[agent_id] = now
        self._agent_respawn_counts[agent_id] += 1
        restart_count = int(self._agent_respawn_counts[agent_id])
        if old_sandbox_id:
            try:
                await terminate_sandbox(old_sandbox_id)
            except Exception:
                pass
            try:
                await asyncio.to_thread(
                    self.runtime.finish_sandbox,
                    self.run_id,
                    sandbox_id=old_sandbox_id,
                    status="failed",
                    error="agent sandbox became stale and was replaced automatically",
                )
            except Exception:
                pass
        try:
            new_sandbox_id = await spawn_agent_sandbox(
                self.run_id,
                run_config,
                self.runtime,
                agent_id=agent_id,
                restart_count=restart_count,
                replaced_sandbox_id=old_sandbox_id or None,
            )
        except Exception as exc:
            await asyncio.to_thread(
                self.runtime.append_log_blocks,
                self.run_id,
                agent_id=agent_id,
                blocks=[
                    {
                        "block_id": f"watchdog:{agent_id}:{restart_count}:failed",
                        "run_id": self.run_id,
                        "agent_id": agent_id,
                        "role": "system",
                        "kind": "error",
                        "title": "Sandbox Restart Failed",
                        "text": (
                            "The agent sandbox became stale and an automatic restart failed: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                        "collapsed": False,
                        "streaming": False,
                        "created_at": now.isoformat(),
                        "updated_at": now.isoformat(),
                    }
                ],
            )
            return
        restart_time = datetime.now(tz=UTC)
        await asyncio.to_thread(
            self.runtime.append_log_blocks,
            self.run_id,
            agent_id=agent_id,
            blocks=[
                {
                    "block_id": f"watchdog:{agent_id}:{restart_count}:restarted",
                    "run_id": self.run_id,
                    "agent_id": agent_id,
                    "sandbox_id": new_sandbox_id,
                    "role": "system",
                    "kind": "summary",
                    "title": "Sandbox Restarted",
                    "text": (
                        "The previous agent sandbox stopped heartbeating and was replaced automatically. "
                        "Resume your prior session, re-check your workspace files, and continue."
                    ),
                    "collapsed": False,
                    "streaming": False,
                    "created_at": restart_time.isoformat(),
                    "updated_at": restart_time.isoformat(),
                }
            ],
        )

    async def _heartbeat_loop(self, run_config: RunConfig) -> None:
        while True:
            await asyncio.to_thread(
                self.runtime.heartbeat_sandbox,
                self.run_id,
                sandbox_id=self.sandbox_id,
                status="running",
                metadata_patch={
                    "current_live_matches": len(self._match_tasks),
                    "peak_live_matches": self._peak_live_matches,
                    "worker_parallelism": self._parallelism(run_config),
                },
                heartbeat_ttl_seconds=max(30, int(max(1.0, run_config.tournament_poll_seconds) * 10)),
            )
            await asyncio.sleep(max(1.0, run_config.tournament_poll_seconds * 10))

    async def _control(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(self.runtime.get_run_control, self.run_id)
        if payload is None:
            raise RuntimeError(f"run {self.run_id} not found")
        return payload

    async def _tournament_state(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(self.runtime.get_tournament_state, self.run_id)
        if payload is None:
            raise RuntimeError(f"run {self.run_id} not found")
        return payload

    def _hydrate_state(self, state: dict[str, Any]) -> None:
        self._agents = {
            str(payload["agent_id"]): AgentState.model_validate(payload)
            for payload in list(state.get("agents") or [])
        }
        self._bots = {
            str(payload["bot_id"]): BotSubmission.model_validate(payload)
            for payload in list(state.get("bots") or [])
        }
        self._completed_pair_counts.clear()
        self._completed_match_counts.clear()
        for bot in self._bots.values():
            self._completed_match_counts[bot.bot_id] = max(0, int(bot.matches_played))

    async def _drain_finished_tasks(self, run_config: RunConfig) -> None:
        done = [task for task in self._match_tasks if task.done()]
        if not done:
            return
        matches: list[MatchResult] = []
        updated_bots: dict[str, BotSubmission] = {}
        updated_agents: dict[str, AgentState] = {}
        log_blocks: dict[str, list[dict[str, Any]]] = {}
        for task in done:
            bot_ids = self._match_tasks.pop(task, [])
            self._inflight_counts.subtract(bot_ids)
            for bot_id in list(self._inflight_counts):
                if self._inflight_counts[bot_id] <= 0:
                    del self._inflight_counts[bot_id]
            if task.cancelled():
                continue
            match = task.result()
            for participant in match.participants:
                self._completed_match_counts[participant.bot_id] += 1
            for left, right in combinations([participant.bot_id for participant in match.participants], 2):
                self._completed_pair_counts[frozenset((left, right))] += 1
            matches.append(match)
            touched_bots, touched_agents = self._apply_ratings(match, run_config)
            for bot in touched_bots:
                updated_bots[bot.bot_id] = bot
            for agent in touched_agents:
                updated_agents[agent.agent_id] = agent
            self._record_bot_failure(match, run_config, updated_bots, updated_agents, log_blocks)
        if matches:
            await asyncio.to_thread(
                self.runtime.append_match_results,
                self.run_id,
                matches=matches,
                bots=updated_bots.values(),
                agents=updated_agents.values(),
            )
        for agent_id, blocks in log_blocks.items():
            await asyncio.to_thread(
                self.runtime.append_log_blocks,
                self.run_id,
                agent_id=agent_id,
                blocks=blocks,
            )

    def _record_bot_failure(
        self,
        match: MatchResult,
        run_config: RunConfig,
        updated_bots: dict[str, BotSubmission],
        updated_agents: dict[str, AgentState],
        log_blocks: dict[str, list[dict[str, Any]]],
    ) -> None:
        if match.status != GameStatus.FORFEIT or not match.loser_bot_id:
            return
        bot = self._bots.get(match.loser_bot_id)
        if bot is None:
            return
        reason = str(match.reason or "unknown runtime failure")
        bot.failure_count += 1
        bot.last_failure_reason = reason
        updated_bots[bot.bot_id] = bot
        threshold = run_config.bot_failure_retirement_threshold
        notice_text: str | None = None
        notice_title = "Bot Runtime Error"
        if bot.failure_count == 1:
            notice_text = (
                f"Bot {bot.name} ({bot.bot_id}) just forfeited due to `{reason}`. "
                f"If this bot reaches {threshold} runtime failures, it will be retired automatically. "
                "Fix the bug and submit a new revision."
            )
        if bot.active and bot.failure_count >= threshold:
            bot.active = False
            bot.retired_reason = (
                f"Retired after {bot.failure_count} runtime failures. Latest failure: {reason}"
            )
            updated_bots[bot.bot_id] = bot
            self._bot_cache.pop(bot.bot_id, None)
            self._refresh_agent_best(self._agents, self._bots, bot.agent_id)
            updated_agents[bot.agent_id] = self._agents[bot.agent_id]
            notice_title = "Bot Retired"
            notice_text = (
                f"Bot {bot.name} ({bot.bot_id}) was retired after {bot.failure_count} runtime failures. "
                f"Latest failure: `{reason}`. Fix the bug and submit a new revision."
            )
        if not notice_text:
            return
        notice_created_at = (
            match.finished_at.isoformat()
            if match.finished_at is not None
            else datetime.now(tz=UTC).isoformat()
        )
        log_blocks.setdefault(bot.agent_id, []).append(
            {
                "block_id": f"{match.game_id}:{bot.bot_id}:failure-{bot.failure_count}",
                "run_id": self.run_id,
                "agent_id": bot.agent_id,
                "role": "system",
                "kind": "error",
                "title": notice_title,
                "text": notice_text,
                "collapsed": False,
                "streaming": False,
                "created_at": notice_created_at,
                "updated_at": notice_created_at,
            }
        )


def _from_millis(value: int | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    return datetime.fromtimestamp(value / 1000, tz=UTC)


async def main_async(run_id: str) -> None:
    configure_logging()
    await TournamentServer(run_id).run()


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m game_of_agents.tournament_server <run_id>")
    asyncio.run(main_async(sys.argv[1]))


if __name__ == "__main__":
    main()
