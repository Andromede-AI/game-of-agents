from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from game_of_agents.events import JsonlEventSink
from game_of_agents.games.poker.engine import PokerEngine
from game_of_agents.models import AgentConfig, AgentRuntime, BotSubmission, BotSubmissionRequest, RunConfig, RunState
from game_of_agents.store import RunStore
from game_of_agents.tournament import TournamentService, _load_staged_bot, update_elo
from game_of_agents.workspaces import WorkspaceManager
from tests.config_factories import make_run_config


def write_bot(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _set_bot_scores(run: RunState, scores: dict[str, float]) -> RunState:
    for bot_id, score in scores.items():
        bot = run.bots[bot_id]
        bot.rating_score = score
        bot.elo = score
        run.bots[bot_id] = bot
    return run


async def create_run(tmp_path: Path) -> tuple[RunStore, WorkspaceManager, TournamentService, RunState]:
    store = RunStore(tmp_path / "runs")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    events = JsonlEventSink(tmp_path / "events")
    run = RunState(
        config=make_run_config(
            name="smoke",
            description="short run",
            concurrent_matches=1,
            match_executor="thread",
            max_active_bots_per_agent=2,
            game={"max_concurrent_matches_per_bot": 1},
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        )
    )
    for agent in run.config.agents:
        run.agents[agent.agent_id] = workspaces.scaffold_agent(run, agent)
    await store.save_run(run)
    return store, workspaces, TournamentService(store, events, workspaces), run


async def create_multiplayer_run(
    tmp_path: Path,
) -> tuple[RunStore, WorkspaceManager, TournamentService, RunState]:
    store = RunStore(tmp_path / "runs")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    events = JsonlEventSink(tmp_path / "events")
    run = RunState(
        config=make_run_config(
            name="multiplayer",
            description="four-player test",
            concurrent_matches=1,
            match_executor="thread",
            max_active_bots_per_agent=2,
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="gamma", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="delta", runtime=AgentRuntime.MOCK),
            ],
            game={
                "players_per_match": 4,
                "max_rounds_per_match": 6,
                "game_time_bank_seconds": 8,
                "action_increment_seconds": 1,
                "max_concurrent_matches_per_bot": 1,
            },
        )
    )
    for agent in run.config.agents:
        run.agents[agent.agent_id] = workspaces.scaffold_agent(run, agent)
    await store.save_run(run)
    return store, workspaces, TournamentService(store, events, workspaces), run


@pytest.mark.asyncio
async def test_run_round_updates_elo_and_records_game(tmp_path: Path) -> None:
    _, _, service, run = await create_run(tmp_path)
    alpha_root = Path(run.agents["alpha"].workspace)
    beta_root = Path(run.agents["beta"].workspace)
    write_bot(
        alpha_root / "alpha_bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class AggressiveBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        if "raise_to" in observation.legal_actions and observation.min_raise_to is not None:
            return BotAction("raise_to", observation.min_raise_to)
        return BotAction("check_call")
""".strip(),
    )
    write_bot(
        beta_root / "beta_bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class CallingBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        return BotAction("check_call")
""".strip(),
    )
    await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="alpha",
            description="raiser",
            entrypoint="AggressiveBot",
            module_path="alpha_bot.py",
        ),
    )
    await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="beta",
            name="beta",
            description="caller",
            entrypoint="CallingBot",
            module_path="beta_bot.py",
        ),
    )

    alpha_bot = next(bot for bot in (await service.active_bots(run.run_id)) if bot.agent_id == "alpha")
    beta_bot = next(bot for bot in (await service.active_bots(run.run_id)) if bot.agent_id == "beta")
    full_match = await service.run_match(run.run_id, [alpha_bot.bot_id, beta_bot.bot_id])
    updated = await service._require_run(run.run_id)

    assert len(updated.games) == 1
    game = next(iter(updated.games.values()))
    assert game.status.value in {"finished", "forfeit"}
    assert game.duration_seconds is not None
    assert game.actions  # actions captured by default in run_match
    leaderboard = await service.leaderboard(run.run_id)
    assert leaderboard["agents"][0]["best_elo"] != 1000.0
    assert leaderboard["bots"][0]["elo"] != leaderboard["bots"][1]["elo"]
    action_events = [event for event in full_match.actions if "time_remaining" in event]
    assert action_events
    assert (
        max(event["time_remaining"] for event in action_events)
        <= run.config.game.game_time_bank_seconds / run.config.game.players_per_match
    )


@pytest.mark.asyncio
async def test_broken_bot_forfeits_match(tmp_path: Path) -> None:
    _, _, service, run = await create_run(tmp_path)
    alpha_root = Path(run.agents["alpha"].workspace)
    beta_root = Path(run.agents["beta"].workspace)
    write_bot(
        alpha_root / "alpha_bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class BrokenBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        raise RuntimeError("boom")
""".strip(),
    )
    write_bot(
        beta_root / "beta_bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class SafeBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        return BotAction("check_call")
""".strip(),
    )
    broken = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="broken",
            description="broken",
            entrypoint="BrokenBot",
            module_path="alpha_bot.py",
        ),
    )
    safe = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="beta",
            name="safe",
            description="safe",
            entrypoint="SafeBot",
            module_path="beta_bot.py",
        ),
    )

    updated = await service.run_round(run.run_id)
    game = next(iter(updated.games.values()))

    assert game.status.value == "forfeit"
    assert game.winner_bot_id == safe.bot_id
    assert game.loser_bot_id == broken.bot_id
    assert "bot exception" in game.reason


@pytest.mark.asyncio
async def test_active_bot_cap_retires_current_worst_version(tmp_path: Path) -> None:
    _, _, service, run = await create_run(tmp_path)
    alpha_root = Path(run.agents["alpha"].workspace)
    write_bot(
        alpha_root / "bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class PassiveBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        return BotAction("check_call")
""".strip(),
    )
    first = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="one",
            description="one",
            entrypoint="PassiveBot",
            module_path="bot.py",
        ),
    )
    second = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="two",
            description="two",
            entrypoint="PassiveBot",
            module_path="bot.py",
        ),
    )
    await service.store.update_run(
        run.run_id,
        lambda current: _set_bot_scores(
            current,
            {
                first.bot_id: 20.0,
                second.bot_id: -5.0,
            },
        ),
    )
    third = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="three",
            description="three",
            entrypoint="PassiveBot",
            module_path="bot.py",
        ),
    )

    bots = await service.active_bots(run.run_id)
    alpha_bots = [bot for bot in bots if bot.agent_id == "alpha"]
    assert {bot.bot_id for bot in alpha_bots} == {first.bot_id, third.bot_id}


@pytest.mark.asyncio
async def test_fill_match_queue_runs_multiple_pairs_in_parallel(tmp_path: Path) -> None:
    _, _, service, run = await create_run(tmp_path)
    alpha_root = Path(run.agents["alpha"].workspace)
    beta_root = Path(run.agents["beta"].workspace)
    bot_body = """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class QueueBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        return BotAction("check_call")
""".strip()
    for index in range(2):
        alpha_path = alpha_root / f"alpha_{index}.py"
        beta_path = beta_root / f"beta_{index}.py"
        write_bot(alpha_path, bot_body)
        write_bot(beta_path, bot_body)
        await service.submit_bot(
            run.run_id,
            BotSubmissionRequest(
                agent_id="alpha",
                name=f"alpha-{index}",
                description="queue alpha",
                entrypoint="QueueBot",
                module_path=str(alpha_path.relative_to(alpha_root)),
            ),
        )
        await service.submit_bot(
            run.run_id,
            BotSubmissionRequest(
                agent_id="beta",
                name=f"beta-{index}",
                description="queue beta",
                entrypoint="QueueBot",
                module_path=str(beta_path.relative_to(beta_root)),
            ),
        )

    scheduled = await service.fill_match_queue(run.run_id, target_concurrency=3)
    await service.wait_for_matches(run.run_id)
    updated = await service._require_run(run.run_id)

    assert scheduled == 2
    assert len(updated.games) == 2
    assert len({tuple(sorted((game.bot_a_id, game.bot_b_id))) for game in updated.games.values()}) == 2


@pytest.mark.asyncio
async def test_fill_match_queue_runs_all_unique_pairs_up_to_capacity(tmp_path: Path) -> None:
    _, _, service, run = await create_run(tmp_path)
    alpha_root = Path(run.agents["alpha"].workspace)
    beta_root = Path(run.agents["beta"].workspace)
    write_bot(
        alpha_root / "alpha_bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class AggressiveBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        if "raise_to" in observation.legal_actions and observation.min_raise_to is not None:
            return BotAction("raise_to", observation.min_raise_to)
        return BotAction("check_call")
""".strip(),
    )
    write_bot(
        beta_root / "beta_bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class CallingBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        return BotAction("check_call")
""".strip(),
    )
    for suffix in ("one", "two"):
        await service.submit_bot(
            run.run_id,
            BotSubmissionRequest(
                agent_id="alpha",
                name=f"alpha-{suffix}",
                description=suffix,
                entrypoint="AggressiveBot",
                module_path="alpha_bot.py",
            ),
        )
        await service.submit_bot(
            run.run_id,
            BotSubmissionRequest(
                agent_id="beta",
                name=f"beta-{suffix}",
                description=suffix,
                entrypoint="CallingBot",
                module_path="beta_bot.py",
            ),
        )

    scheduled = await service.fill_match_queue(run.run_id, target_concurrency=10)
    await service.wait_for_matches(run.run_id)
    scheduled += await service.fill_match_queue(run.run_id, target_concurrency=10)
    await service.wait_for_matches(run.run_id)
    updated = await service._require_run(run.run_id)

    assert scheduled == 4
    assert len(updated.games) == 4
    assert {
        tuple(sorted((game.bot_a_id, game.bot_b_id)))
        for game in updated.games.values()
    } == {
        tuple(sorted((alpha.bot_id, beta.bot_id)))
        for alpha in updated.bots.values()
        if alpha.agent_id == "alpha" and alpha.active
        for beta in updated.bots.values()
        if beta.agent_id == "beta" and beta.active
    }


@pytest.mark.asyncio
async def test_same_bot_can_play_multiple_concurrent_matches_when_unbounded(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    events = JsonlEventSink(tmp_path / "events")
    run = RunState(
        config=make_run_config(
            name="parallel-bot-reuse",
            description="allow same bot in many live matches",
            concurrent_matches=3,
            match_executor="thread",
            match_worker_threads=3,
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
            game={
                "players_per_match": 2,
                "max_rounds_per_match": 6,
                "game_time_bank_seconds": 8,
                "action_increment_seconds": 0,
                "max_concurrent_matches_per_bot": None,
            },
        )
    )
    for agent in run.config.agents:
        run.agents[agent.agent_id] = workspaces.scaffold_agent(run, agent)
    await store.save_run(run)
    service = TournamentService(store, events, workspaces)

    slow_bot = """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation
import time

class SlowBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        time.sleep(0.03)
        return BotAction("check_call")
""".strip()
    alpha_path = Path(run.agents["alpha"].workspace) / "alpha.py"
    beta_path = Path(run.agents["beta"].workspace) / "beta.py"
    write_bot(alpha_path, slow_bot)
    write_bot(beta_path, slow_bot)
    alpha = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="alpha",
            description="slow",
            entrypoint="SlowBot",
            module_path="alpha.py",
        ),
    )
    beta = await service.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="beta",
            name="beta",
            description="slow",
            entrypoint="SlowBot",
            module_path="beta.py",
        ),
    )

    launched = await service.fill_match_queue(run.run_id, target_concurrency=3)

    assert launched == 3
    await asyncio.sleep(0.02)
    assert len(service._match_tasks[run.run_id]) == 3
    inflight = service._inflight_bot_counts[run.run_id]
    assert inflight[alpha.bot_id] == 3
    assert inflight[beta.bot_id] == 3

    await service.wait_for_matches(run.run_id)
    updated = await service._require_run(run.run_id)
    assert len(updated.games) == 3


@pytest.mark.asyncio
async def test_fill_match_queue_caps_live_matches_to_worker_parallelism(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    events = JsonlEventSink(tmp_path / "events")
    run = RunState(
        config=make_run_config(
            name="parallelism-cap",
            description="do not queue deeper than worker count",
            concurrent_matches=5,
            match_executor="thread",
            match_worker_threads=2,
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
            game={
                "players_per_match": 2,
                "max_rounds_per_match": 6,
                "game_time_bank_seconds": 8,
                "action_increment_seconds": 0,
                "max_concurrent_matches_per_bot": None,
            },
        )
    )
    for agent in run.config.agents:
        run.agents[agent.agent_id] = workspaces.scaffold_agent(run, agent)
    await store.save_run(run)
    service = TournamentService(store, events, workspaces)

    slow_bot = """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation
import time

class SlowBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        time.sleep(0.03)
        return BotAction("check_call")
""".strip()
    for agent_id in ("alpha", "beta"):
        bot_path = Path(run.agents[agent_id].workspace) / "bot.py"
        write_bot(bot_path, slow_bot)
        await service.submit_bot(
            run.run_id,
            BotSubmissionRequest(
                agent_id=agent_id,
                name=agent_id,
                description="slow",
                entrypoint="SlowBot",
                module_path="bot.py",
            ),
        )

    launched = await service.fill_match_queue(run.run_id, target_concurrency=5)

    assert launched == 2
    assert len(service._match_tasks[run.run_id]) == 2

    await service.wait_for_matches(run.run_id)


@pytest.mark.asyncio
async def test_multiplayer_freezeout_updates_trueskill_and_placements(tmp_path: Path) -> None:
    _, _, service, run = await create_multiplayer_run(tmp_path)
    bot_body = """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class TableBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        if "raise_to" in observation.legal_actions and observation.min_raise_to is not None and observation.round_index == 0:
            return BotAction("raise_to", observation.min_raise_to)
        return BotAction("check_call")
""".strip()

    for agent_id in ("alpha", "beta", "gamma", "delta"):
        root = Path(run.agents[agent_id].workspace)
        write_bot(root / "bot.py", bot_body)
        await service.submit_bot(
            run.run_id,
            BotSubmissionRequest(
                agent_id=agent_id,
                name=agent_id,
                description=f"{agent_id} bot",
                entrypoint="TableBot",
                module_path="bot.py",
            ),
        )

    updated = await service.run_round(run.run_id)

    assert len(updated.games) == 1
    game = next(iter(updated.games.values()))
    assert game.table_size == 4
    assert game.round_count >= 1
    assert len(game.participants) == 4
    assert sorted(participant.placement for participant in game.participants) == [1, 2, 3, 4]
    leaderboard = await service.leaderboard(run.run_id)
    assert all(bot["rating_mu"] != 25.0 or bot["rating_sigma"] != 25.0 / 3.0 for bot in leaderboard["bots"])


def test_update_elo_draw_is_stable() -> None:
    elo_a, elo_b = update_elo(1000.0, 1000.0, 0.5, 24.0)
    assert elo_a == 1000.0
    assert elo_b == 1000.0


def test_load_staged_bot_uses_downloaded_bundle_when_module_path_is_absolute(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    write_bot(
        bundle_root / "bot.py",
        """
from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation

class WorkspaceBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        return BotAction("fold")
""".strip(),
    )
    submission = BotSubmission(
        agent_id="alpha",
        name="alpha",
        description="absolute path regression",
        entrypoint="WorkspaceBot",
        module_path=str(tmp_path / "outside" / "bot.py"),
        artifacts=[],
    )
    loaded = _load_staged_bot(PokerEngine(), submission, str(bundle_root), tmp_path / "staged")
    assert type(loaded.bot).__name__ == "WorkspaceBot"
