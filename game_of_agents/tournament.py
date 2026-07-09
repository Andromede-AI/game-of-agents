from __future__ import annotations

import asyncio
from collections import Counter
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from itertools import combinations
from math import fabs
import multiprocessing as mp
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import HTTPException, status

from game_of_agents.events import EventSink
from game_of_agents.games.poker.engine import PokerEngine, PokerEngineConfig
from game_of_agents.models import (
    BotSubmission,
    BotSubmissionRequest,
    EventRecord,
    GameStatus,
    MatchParticipantResult,
    MatchResult,
    RunConfig,
)
from game_of_agents.rating import build_openskill_model, create_rating, update_bot_rating
from game_of_agents.store import RunStore
from game_of_agents.workspaces import WorkspaceManager


def update_elo(
    rating_a: float,
    rating_b: float,
    score_a: float,
    k_factor: float = 24.0,
) -> tuple[float, float]:
    expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    expected_b = 1 / (1 + 10 ** ((rating_a - rating_b) / 400))
    return (
        rating_a + k_factor * (score_a - expected_a),
        rating_b + k_factor * ((1 - score_a) - expected_b),
    )


def _execute_match_job(
    run_id: str,
    bot_payloads: list[dict[str, Any]],
    workspaces: list[str],
    config_payload: dict[str, Any],
    capture_actions: bool,
) -> dict[str, Any]:
    bots = [BotSubmission.model_validate(payload) for payload in bot_payloads]
    config = RunConfig.model_validate(config_payload)
    engine = PokerEngine(
        PokerEngineConfig(
            starting_stack=config.game.starting_stack,
            small_blind=config.game.small_blind,
            big_blind=config.game.big_blind,
            ante=config.game.ante,
            min_bet=config.game.min_bet,
            time_bank_seconds=config.game.game_time_bank_seconds,
            action_increment_seconds=config.game.action_increment_seconds,
            max_rounds_per_match=config.game.max_rounds_per_match,
        )
    )
    try:
        with TemporaryDirectory(prefix=f"goa-match-{run_id}-") as root:
            match_root = Path(root)
            loaded_players = [
                _load_staged_bot(engine, bot, workspace, match_root / bot.bot_id)
                for bot, workspace in zip(bots, workspaces, strict=True)
            ]
            result = engine.play_match(run_id, loaded_players)
    except Exception as exc:  # pragma: no cover - defensive loader path
        result = MatchResult(
            run_id=run_id,
            status=GameStatus.FORFEIT,
            table_size=len(bots),
            bot_a_id=bots[0].bot_id if bots else None,
            bot_b_id=bots[1].bot_id if len(bots) > 1 else None,
            agent_a_id=bots[0].agent_id if bots else None,
            agent_b_id=bots[1].agent_id if len(bots) > 1 else None,
            reason=f"loader_error:{type(exc).__name__}",
        )
        result.participants = [
            MatchParticipantResult(
                bot_id=bot.bot_id,
                agent_id=bot.agent_id,
                seat=index,
                placement=len(bots) if index == 0 else index,
                ending_chips=config.game.starting_stack if index != 0 else 0,
                eliminated_round=0 if index == 0 else None,
            )
            for index, bot in enumerate(bots, start=1)
        ]
        result.winner_bot_id = bots[1].bot_id if len(bots) > 1 else None
        result.loser_bot_id = bots[0].bot_id if bots else None
        result.finished_at = result.started_at
        result.duration_seconds = 0.0
    if not capture_actions:
        result = _compact_match_result(result)
    return result.model_dump(mode="json")


def _load_staged_bot(
    engine: PokerEngine,
    submission: BotSubmission,
    workspace: str,
    root: Path,
):
    root.mkdir(parents=True, exist_ok=True)
    for artifact in submission.artifacts:
        target = root / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")

    module_path = _module_relative_path(submission, workspace)
    staged_module = root / module_path
    if not staged_module.exists():
        bundled_source = Path(workspace) / module_path
        source = bundled_source if bundled_source.exists() else Path(submission.module_path)
        if not source.exists():
            raise FileNotFoundError(submission.module_path)
        staged_module.parent.mkdir(parents=True, exist_ok=True)
        staged_module.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return engine.load_bot(submission, staged_module, root)


def _module_relative_path(submission: BotSubmission, workspace: str) -> Path:
    module_path = Path(submission.module_path)
    if not module_path.is_absolute():
        return module_path
    workspace_root = Path(workspace).resolve()
    try:
        return module_path.resolve().relative_to(workspace_root)
    except ValueError:
        return Path(module_path.name)


def _compact_match_result(match: MatchResult) -> MatchResult:
    compact = match.model_copy(deep=True)
    compact.actions = []
    return compact


class TournamentService:
    def __init__(self, store: RunStore, events: EventSink, workspaces: WorkspaceManager) -> None:
        self.store = store
        self.events = events
        self.workspaces = workspaces
        self._match_tasks: dict[str, set[asyncio.Task[MatchResult]]] = {}
        self._inflight_bot_counts: dict[str, Counter[str]] = {}
        self._completed_pair_counts: dict[str, Counter[frozenset[str]]] = {}
        self._executors: dict[str, Executor] = {}
        self._executor_workers: dict[str, int] = {}

    async def submit_bot(self, run_id: str, request: BotSubmissionRequest) -> BotSubmission:
        run = await self._require_run(run_id)
        agent = run.agents.get(request.agent_id)
        if agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
        module_path = Path(request.module_path)
        if not module_path.is_absolute():
            module_path = Path(agent.workspace) / module_path
        submission = BotSubmission(
            agent_id=request.agent_id,
            name=request.name,
            description=request.description,
            entrypoint=request.entrypoint,
            module_path=str(module_path),
            artifacts=request.artifacts or self.workspaces.default_artifacts(agent),
        )
        await self.store.update_run(run_id, lambda current: self._apply_submission(current, submission))
        await self.events.emit(
            EventRecord(run_id=run_id, kind="bot.submitted", payload=submission.model_dump(mode="json"))
        )
        return submission

    async def active_bots(self, run_id: str) -> list[BotSubmission]:
        run = await self._require_run(run_id)
        return [bot for bot in run.bots.values() if bot.active]

    async def list_games(self, run_id: str) -> list[dict[str, object]]:
        run = await self._require_run(run_id)
        return [
            {
                "game_id": game.game_id,
                "status": game.status.value,
                "winner_bot_id": game.winner_bot_id,
                "reason": game.reason,
                "table_size": game.table_size,
                "round_count": game.round_count,
            }
            for game in sorted(run.games.values(), key=lambda item: item.started_at, reverse=True)
        ]

    async def leaderboard(self, run_id: str) -> dict[str, object]:
        run = await self._require_run(run_id)
        bots = sorted(run.bots.values(), key=lambda bot: bot.rating_score, reverse=True)
        agents = []
        for agent in run.agents.values():
            active = [bot for bot in bots if bot.agent_id == agent.agent_id and bot.active]
            best = active[0] if active else None
            agents.append(
                {
                    "agent_id": agent.agent_id,
                    "best_bot_id": best.bot_id if best else None,
                    "best_rating_score": round(best.rating_score, 3) if best else 0.0,
                    "best_rating_mu": round(best.rating_mu, 3) if best else agent.best_rating_mu,
                    "best_rating_sigma": round(best.rating_sigma, 3) if best else agent.best_rating_sigma,
                    "best_elo": round(best.elo, 3) if best else agent.best_elo,
                }
            )
        agents.sort(key=lambda item: item["best_rating_score"], reverse=True)
        return {
            "agents": agents,
            "bots": [
                {
                    "bot_id": bot.bot_id,
                    "agent_id": bot.agent_id,
                    "rating_score": round(bot.rating_score, 3),
                    "rating_mu": round(bot.rating_mu, 3),
                    "rating_sigma": round(bot.rating_sigma, 3),
                    "elo": round(bot.elo, 3),
                    "active": bot.active,
                    "name": bot.name,
                }
                for bot in bots
            ],
        }

    async def run_match(self, run_id: str, bot_ids: list[str], *, capture_actions: bool = True) -> MatchResult:
        run = await self._require_run(run_id)
        bots = [run.bots[bot_id].model_copy(deep=True) for bot_id in bot_ids]
        workspaces = [run.agents[bot.agent_id].workspace for bot in bots]
        loop = asyncio.get_running_loop()
        match_payload = await loop.run_in_executor(
            self._executor_for(run),
            _execute_match_job,
            run_id,
            [bot.model_dump(mode="json") for bot in bots],
            workspaces,
            run.config.model_dump(mode="json"),
            capture_actions,
        )
        match_result = MatchResult.model_validate(match_payload)
        stored_match = match_result if capture_actions else _compact_match_result(match_result)
        await self.store.update_run(run_id, lambda current: self._apply_match_result(current, stored_match))
        await self.events.emit(
            EventRecord(run_id=run_id, kind="game.finished", payload=stored_match.model_dump(mode="json"))
        )
        return match_result

    async def fill_match_queue(self, run_id: str, target_concurrency: int | None = None) -> int:
        self._prune_finished_tasks(run_id)
        run = await self._require_run(run_id)
        match_tasks = self._match_tasks.setdefault(run_id, set())
        target = min(
            target_concurrency or run.config.concurrent_matches,
            self._match_parallelism(run, target_concurrency or run.config.concurrent_matches),
        )
        open_slots = max(0, target - len(match_tasks))
        if open_slots == 0:
            return 0
        self._executor_for(run, target)

        inflight_counts = self._inflight_bot_counts.setdefault(run_id, Counter())
        tables = self._pick_tables(run, open_slots, inflight_counts)
        for table in tables:
            bot_ids = [bot.bot_id for bot in table]
            inflight_counts.update(bot_ids)
            task = asyncio.create_task(
                self.run_match(run_id, [bot.bot_id for bot in table], capture_actions=run.config.capture_actions)
            )
            match_tasks.add(task)
            task.add_done_callback(
                lambda done, rid=run_id, ids=bot_ids: self._release_match_task(rid, ids, done)
            )
        return len(tables)

    async def wait_for_matches(self, run_id: str) -> None:
        while True:
            self._prune_finished_tasks(run_id)
            tasks = list(self._match_tasks.get(run_id, set()))
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_round(self, run_id: str):
        await self.fill_match_queue(run_id)
        await self.wait_for_matches(run_id)
        return await self._require_run(run_id)

    def _pick_tables(
        self,
        run,
        limit: int,
        inflight_counts: Counter[str],
    ) -> list[list[BotSubmission]]:
        active = [bot for bot in run.bots.values() if bot.active]
        if len(active) < run.config.game.players_per_match:
            return []
        active.sort(key=lambda bot: bot.rating_score, reverse=True)
        projected_counts = Counter(inflight_counts)
        pair_counts = Counter(self._completed_pair_counts_for(run))
        tables: list[list[BotSubmission]] = []
        for _ in range(limit):
            table = None
            for seed in self._seed_candidates(run, active, projected_counts, pair_counts):
                candidate_table = self._build_table(run, seed, active, projected_counts, pair_counts)
                if len(candidate_table) == run.config.game.players_per_match:
                    table = candidate_table
                    break
            if table is None:
                break
            for left, right in combinations([bot.bot_id for bot in table], 2):
                pair_counts[frozenset((left, right))] += 1
            tables.append(table)
            projected_counts.update(bot.bot_id for bot in table)
        return tables

    def _seed_candidates(
        self,
        run,
        active: list[BotSubmission],
        inflight_counts: Counter[str],
        pair_counts: Counter[frozenset[str]],
    ) -> list[BotSubmission]:
        max_per_bot = run.config.game.max_concurrent_matches_per_bot
        eligible = [
            bot
            for bot in active
            if max_per_bot is None or inflight_counts[bot.bot_id] < max_per_bot
        ]
        return sorted(
            eligible,
            key=lambda bot: (
                inflight_counts[bot.bot_id],
                sum(
                    count
                    for pair, count in pair_counts.items()
                    if bot.bot_id in pair
                ),
                -bot.rating_score,
                bot.bot_id,
            ),
        )

    def _build_table(
        self,
        run,
        seed: BotSubmission,
        active: list[BotSubmission],
        inflight_counts: Counter[str],
        pair_counts: Counter[frozenset[str]],
    ) -> list[BotSubmission]:
        table = [seed]
        used_agents = {seed.agent_id}
        spread = run.config.rating.matchmaking_spread
        max_per_bot = run.config.game.max_concurrent_matches_per_bot
        while len(table) < run.config.game.players_per_match:
            candidates = []
            for candidate in active:
                if candidate.bot_id == seed.bot_id:
                    continue
                if any(existing.bot_id == candidate.bot_id for existing in table):
                    continue
                if max_per_bot is not None and inflight_counts[candidate.bot_id] >= max_per_bot:
                    continue
                if not run.config.game.allow_same_agent_table and candidate.agent_id in used_agents:
                    continue
                if len(table) > 1 and fabs(candidate.rating_score - seed.rating_score) > spread:
                    continue
                repeat_score = sum(
                    pair_counts[frozenset((candidate.bot_id, existing.bot_id))]
                    for existing in table
                )
                candidates.append((repeat_score, fabs(candidate.rating_score - seed.rating_score), candidate))
            if not candidates:
                break
            _, _, chosen = min(candidates, key=lambda item: (item[0], item[1], item[2].bot_id))
            table.append(chosen)
            used_agents.add(chosen.agent_id)
        return table

    def _enforce_cap(self, run, agent_id: str) -> None:
        bots = [bot for bot in run.bots.values() if bot.agent_id == agent_id and bot.active]
        while len(bots) > run.config.max_active_bots_per_agent:
            worst = min(
                bots,
                key=lambda bot: (bot.rating_score, bot.elo, bot.created_at, bot.bot_id),
            )
            worst.active = False
            run.bots[worst.bot_id] = worst
            bots = [bot for bot in bots if bot.bot_id != worst.bot_id]
        self._refresh_best(run, agent_id)

    def _refresh_best(self, run, agent_id: str) -> None:
        active = sorted(
            [bot for bot in run.bots.values() if bot.agent_id == agent_id and bot.active],
            key=lambda bot: bot.rating_score,
            reverse=True,
        )
        agent = run.agents[agent_id]
        if active:
            agent.best_bot_id = active[0].bot_id
            agent.best_rating_mu = active[0].rating_mu
            agent.best_rating_sigma = active[0].rating_sigma
            agent.best_rating_score = active[0].rating_score
            agent.best_elo = active[0].elo
        run.agents[agent_id] = agent

    async def _require_run(self, run_id: str):
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return run

    def _apply_submission(self, run, submission: BotSubmission):
        run.bots[submission.bot_id] = submission
        self._enforce_cap(run, submission.agent_id)
        return run

    def _apply_match_result(self, run, match: MatchResult):
        run.games[match.game_id] = match
        pair_counts = self._completed_pair_counts.setdefault(run.run_id, Counter())
        participants = [participant.bot_id for participant in match.participants]
        for left, right in combinations(participants, 2):
            pair_counts[frozenset((left, right))] += 1
        model = build_openskill_model(run.config.rating)
        teams = []
        placements = []
        by_bot_id = {bot.bot_id: bot for bot in run.bots.values()}
        for participant in sorted(match.participants, key=lambda item: item.placement):
            bot = by_bot_id.get(participant.bot_id)
            if bot is None:
                continue
            teams.append([create_rating(model, bot.rating_mu, bot.rating_sigma)])
            placements.append(participant.placement - 1)
        if teams:
            updated = model.rate(teams, ranks=placements)
            for participant, new_rating in zip(sorted(match.participants, key=lambda item: item.placement), updated):
                bot = run.bots.get(participant.bot_id)
                if bot is None:
                    continue
                update_bot_rating(bot, run.config.rating, new_rating[0])
                run.bots[bot.bot_id] = bot
                self._refresh_best(run, bot.agent_id)
        return run

    def _release_match_task(self, run_id: str, bot_ids: list[str], task: asyncio.Task[MatchResult]) -> None:
        tasks = self._match_tasks.get(run_id)
        if tasks is not None:
            tasks.discard(task)
        inflight = self._inflight_bot_counts.get(run_id)
        if inflight is not None:
            inflight.subtract(bot_ids)
            for bot_id in list(inflight):
                if inflight[bot_id] <= 0:
                    del inflight[bot_id]

    def _executor_for(self, run, desired_parallelism: int | None = None) -> Executor:
        workers = self._match_parallelism(run, desired_parallelism or run.config.concurrent_matches)
        executor = self._executors.get(run.run_id)
        existing_workers = self._executor_workers.get(run.run_id)
        if (
            executor is not None
            and existing_workers is not None
            and workers > existing_workers
            and not self._match_tasks.get(run.run_id)
        ):
            executor.shutdown(wait=True, cancel_futures=True)
            executor = None
            self._executors.pop(run.run_id, None)
            self._executor_workers.pop(run.run_id, None)
        if executor is None:
            if run.config.match_executor == "thread":
                executor = ThreadPoolExecutor(
                    max_workers=workers,
                    thread_name_prefix=f"goa-match-{run.run_id[-4:]}",
                )
            else:
                kwargs: dict[str, Any] = {"max_workers": workers}
                if os.name != "nt":
                    kwargs["mp_context"] = mp.get_context("forkserver")
                executor = ProcessPoolExecutor(**kwargs)
            self._executors[run.run_id] = executor
            self._executor_workers[run.run_id] = workers
        return executor

    async def shutdown_run(self, run_id: str) -> None:
        executor = self._executors.pop(run_id, None)
        self._completed_pair_counts.pop(run_id, None)
        self._executor_workers.pop(run_id, None)
        if executor is None:
            return
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)

    def _prune_finished_tasks(self, run_id: str) -> None:
        tasks = self._match_tasks.get(run_id)
        if not tasks:
            return
        self._match_tasks[run_id] = {task for task in tasks if not task.done()}

    def _completed_pair_counts_for(self, run) -> Counter[frozenset[str]]:
        cached = self._completed_pair_counts.get(run.run_id)
        if cached is not None:
            return cached
        counts: Counter[frozenset[str]] = Counter()
        for game in run.games.values():
            participants = [part.bot_id for part in game.participants]
            for left, right in combinations(participants, 2):
                counts[frozenset((left, right))] += 1
        self._completed_pair_counts[run.run_id] = counts
        return counts

    def _match_parallelism(self, run, requested_parallelism: int) -> int:
        return (
            run.config.match_worker_processes
            or run.config.match_worker_threads
            or min(os.cpu_count() or 1, requested_parallelism)
        )
