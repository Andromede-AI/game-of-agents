from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from game_of_agents.models import AgentConfig, AgentRuntime, AgentState, BotSubmission, GameStatus, MatchParticipantResult, MatchResult, RunConfig
from game_of_agents.tournament_server import TournamentServer


def test_seed_candidates_prioritize_fresh_under_tested_bots(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    run_config = RunConfig(
        name="seed-priority",
        description="seed priority",
        agents=[
            AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
            AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
        ],
    )
    now = datetime.now(tz=UTC)
    stale = BotSubmission(
        agent_id="alpha",
        name="stale",
        description="older bot",
        entrypoint="WorkspaceBot",
        module_path="bot.py",
        created_at=now - timedelta(minutes=5),
    )
    fresh = BotSubmission(
        agent_id="beta",
        name="fresh",
        description="fresh bot",
        entrypoint="WorkspaceBot",
        module_path="bot.py",
        created_at=now,
    )
    server._completed_match_counts[stale.bot_id] = 12
    server._completed_match_counts[fresh.bot_id] = 0

    ordered = server._seed_candidates([stale, fresh], Counter(), run_config)

    assert [bot.bot_id for bot in ordered[:2]] == [fresh.bot_id, stale.bot_id]


def test_enforce_cap_keeps_fresh_submission_active(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    run_config = RunConfig(
        name="cap-fresh",
        description="cap fresh",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        max_active_bots_per_agent=3,
    )
    agent = AgentState(agent_id="alpha", runtime=AgentRuntime.MOCK, internet_access=False, workspace="/tmp/alpha")
    now = datetime.now(tz=UTC)
    incumbent_a = BotSubmission(
        agent_id="alpha",
        name="incumbent-a",
        description="",
        entrypoint="WorkspaceBot",
        module_path="bot.py",
        created_at=now - timedelta(minutes=3),
        matches_played=12,
        rating_score=18.0,
        elo=1800,
    )
    incumbent_b = BotSubmission(
        agent_id="alpha",
        name="incumbent-b",
        description="",
        entrypoint="WorkspaceBot",
        module_path="bot.py",
        created_at=now - timedelta(minutes=2),
        matches_played=12,
        rating_score=12.0,
        elo=1600,
    )
    incumbent_c = BotSubmission(
        agent_id="alpha",
        name="incumbent-c",
        description="",
        entrypoint="WorkspaceBot",
        module_path="bot.py",
        created_at=now - timedelta(minutes=1),
        matches_played=12,
        rating_score=6.0,
        elo=1400,
    )
    fresh = BotSubmission(
        agent_id="alpha",
        name="fresh",
        description="",
        entrypoint="WorkspaceBot",
        module_path="bot.py",
        created_at=now,
        matches_played=0,
        rating_score=0.0,
        elo=1000,
    )
    server._agents = {agent.agent_id: agent}
    server._bots = {
        incumbent_a.bot_id: incumbent_a,
        incumbent_b.bot_id: incumbent_b,
        incumbent_c.bot_id: incumbent_c,
        fresh.bot_id: fresh,
    }

    evicted = server._enforce_cap(run_config, server._agents, server._bots, "alpha", max_evictions=1)

    assert evicted == [incumbent_c.bot_id]
    assert server._bots[fresh.bot_id].active is True
    assert server._bots[incumbent_c.bot_id].active is False


def test_stale_agent_candidates_pick_latest_stale_per_agent(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    run_config = RunConfig(
        name="stale-watchdog",
        description="stale watchdog",
        agents=[
            AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
            AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
        ],
    )
    now = datetime.now(tz=UTC)

    candidates = server._stale_agent_candidates(
        [
            {"kind": "agent", "agentId": "alpha", "sandboxId": "sb-old", "status": "stale", "lastHeartbeatAt": 100},
            {"kind": "agent", "agentId": "alpha", "sandboxId": "sb-new", "status": "running", "lastHeartbeatAt": 200},
            {"kind": "agent", "agentId": "beta", "sandboxId": "sb-beta", "status": "stale", "lastHeartbeatAt": 300},
        ],
        run_config,
        now=now,
    )

    assert candidates == [("beta", {"kind": "agent", "agentId": "beta", "sandboxId": "sb-beta", "status": "stale", "lastHeartbeatAt": 300})]


@pytest.mark.asyncio
async def test_respawn_agent_replaces_stale_sandbox_and_logs_notice(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    run_config = RunConfig(
        name="respawn",
        description="respawn",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
    )
    server.runtime = SimpleNamespace()
    server.runtime.finish_sandbox = lambda *args, **kwargs: None
    log_calls: list[dict[str, object]] = []

    def append_log_blocks(run_id: str, *, agent_id: str, blocks):
        log_calls.append({"run_id": run_id, "agent_id": agent_id, "blocks": list(blocks)})

    server.runtime.append_log_blocks = append_log_blocks
    terminated: list[str] = []
    spawned: list[dict[str, object]] = []

    async def fake_terminate(sandbox_id: str) -> bool:
        terminated.append(sandbox_id)
        return True

    async def fake_spawn(run_id: str, config: RunConfig, runtime, *, agent_id: str, restart_count: int, replaced_sandbox_id: str | None):
        spawned.append(
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "restart_count": restart_count,
                "replaced_sandbox_id": replaced_sandbox_id,
            }
        )
        return "sb_replacement"

    monkeypatch.setattr("game_of_agents.tournament_server.terminate_sandbox", fake_terminate)
    monkeypatch.setattr("game_of_agents.tournament_server.spawn_agent_sandbox", fake_spawn)

    await server._respawn_agent(
        "alpha",
        {"sandboxId": "sb_stale", "status": "stale", "kind": "agent", "agentId": "alpha"},
        run_config,
    )

    assert terminated == ["sb_stale"]
    assert spawned == [
        {
            "run_id": "run_test",
            "agent_id": "alpha",
            "restart_count": 1,
            "replaced_sandbox_id": "sb_stale",
        }
    ]
    assert log_calls and log_calls[0]["agent_id"] == "alpha"
    assert "replaced automatically" in log_calls[0]["blocks"][0]["text"]


@pytest.mark.asyncio
async def test_drain_finished_tasks_batches_convex_writes(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    run_config = RunConfig(
        name="batch-drain",
        description="batch drain",
        agents=[
            AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
            AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
        ],
        game={"players_per_match": 2},
    )
    alpha = AgentState(agent_id="alpha", runtime=AgentRuntime.MOCK, internet_access=False, workspace="/tmp/alpha")
    beta = AgentState(agent_id="beta", runtime=AgentRuntime.MOCK, internet_access=False, workspace="/tmp/beta")
    bot_a = BotSubmission(agent_id="alpha", name="alpha", description="", entrypoint="WorkspaceBot", module_path="bot.py")
    bot_b = BotSubmission(agent_id="beta", name="beta", description="", entrypoint="WorkspaceBot", module_path="bot.py")
    server._agents = {alpha.agent_id: alpha, beta.agent_id: beta}
    server._bots = {bot_a.bot_id: bot_a, bot_b.bot_id: bot_b}
    server.runtime = SimpleNamespace()
    calls: list[dict[str, object]] = []

    def append_match_results(run_id: str, *, matches, bots, agents):
        calls.append(
            {
                "run_id": run_id,
                "matches": list(matches),
                "bots": list(bots),
                "agents": list(agents),
            }
        )

    server.runtime.append_match_results = append_match_results

    first = MatchResult(
        run_id="run_test",
        table_size=2,
        participants=[
            MatchParticipantResult(bot_id=bot_a.bot_id, agent_id="alpha", seat=1, placement=1, ending_chips=120),
            MatchParticipantResult(bot_id=bot_b.bot_id, agent_id="beta", seat=2, placement=2, ending_chips=80),
        ],
    )
    second = MatchResult(
        run_id="run_test",
        table_size=2,
        participants=[
            MatchParticipantResult(bot_id=bot_a.bot_id, agent_id="alpha", seat=1, placement=2, ending_chips=70),
            MatchParticipantResult(bot_id=bot_b.bot_id, agent_id="beta", seat=2, placement=1, ending_chips=130),
        ],
    )
    task_a = asyncio.create_task(asyncio.sleep(0, result=first))
    task_b = asyncio.create_task(asyncio.sleep(0, result=second))
    await asyncio.gather(task_a, task_b)
    server._match_tasks = {
        task_a: [bot_a.bot_id, bot_b.bot_id],
        task_b: [bot_a.bot_id, bot_b.bot_id],
    }
    server._inflight_counts.update([bot_a.bot_id, bot_b.bot_id, bot_a.bot_id, bot_b.bot_id])

    await server._drain_finished_tasks(run_config)

    assert len(calls) == 1
    assert len(calls[0]["matches"]) == 2
    assert {bot.bot_id for bot in calls[0]["bots"]} == {bot_a.bot_id, bot_b.bot_id}
    assert {agent.agent_id for agent in calls[0]["agents"]} == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_repeated_bot_failures_retire_bot_and_notify_agent(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    run_config = RunConfig(
        name="bot-failures",
        description="bot failure retirement",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        game={"players_per_match": 2},
        bot_failure_retirement_threshold=2,
    )
    agent = AgentState(agent_id="alpha", runtime=AgentRuntime.MOCK, internet_access=False, workspace="/tmp/alpha")
    bot = BotSubmission(agent_id="alpha", name="alpha-bot", description="", entrypoint="WorkspaceBot", module_path="bot.py")
    opponent = BotSubmission(agent_id="beta", name="beta-bot", description="", entrypoint="WorkspaceBot", module_path="bot.py")
    server._agents = {
        agent.agent_id: agent,
        "beta": AgentState(agent_id="beta", runtime=AgentRuntime.MOCK, internet_access=False, workspace="/tmp/beta"),
    }
    server._bots = {bot.bot_id: bot, opponent.bot_id: opponent}
    server.runtime = SimpleNamespace()
    match_calls: list[dict[str, object]] = []
    log_calls: list[dict[str, object]] = []

    def append_match_results(run_id: str, *, matches, bots, agents):
        match_calls.append(
            {
                "run_id": run_id,
                "matches": list(matches),
                "bots": list(bots),
                "agents": list(agents),
            }
        )

    def append_log_blocks(run_id: str, *, agent_id: str, blocks):
        log_calls.append({"run_id": run_id, "agent_id": agent_id, "blocks": list(blocks)})

    server.runtime.append_match_results = append_match_results
    server.runtime.append_log_blocks = append_log_blocks

    failed_match = MatchResult(
        run_id="run_test",
        status=GameStatus.FORFEIT,
        reason="bot exception: list index out of range",
        loser_bot_id=bot.bot_id,
        winner_bot_id=opponent.bot_id,
        table_size=2,
        participants=[
            MatchParticipantResult(bot_id=bot.bot_id, agent_id="alpha", seat=1, placement=2, ending_chips=0, eliminated_round=1),
            MatchParticipantResult(bot_id=opponent.bot_id, agent_id="beta", seat=2, placement=1, ending_chips=200),
        ],
    )
    second_failed_match = failed_match.model_copy(deep=True)
    second_failed_match.game_id = "game_second"

    task_a = asyncio.create_task(asyncio.sleep(0, result=failed_match))
    task_b = asyncio.create_task(asyncio.sleep(0, result=second_failed_match))
    await asyncio.gather(task_a, task_b)
    server._match_tasks = {
        task_a: [bot.bot_id, opponent.bot_id],
        task_b: [bot.bot_id, opponent.bot_id],
    }
    server._inflight_counts.update([bot.bot_id, opponent.bot_id, bot.bot_id, opponent.bot_id])

    await server._drain_finished_tasks(run_config)

    assert match_calls
    assert server._bots[bot.bot_id].failure_count == 2
    assert server._bots[bot.bot_id].active is False
    assert server._bots[bot.bot_id].retired_reason is not None
    assert log_calls and log_calls[0]["agent_id"] == "alpha"
    texts = [block["text"] for block in log_calls[0]["blocks"]]
    assert any("forfeited due to" in text for text in texts)
    assert any("was retired after 2 runtime failures" in text for text in texts)
    assert all(isinstance(block["created_at"], str) for block in log_calls[0]["blocks"])


@pytest.mark.asyncio
async def test_tournament_announce_sandbox_prefers_heartbeat(monkeypatch) -> None:
    monkeypatch.setattr("game_of_agents.tournament_server.settings.convex_url", "https://example.convex.cloud")
    server = TournamentServer("run_test")
    server.runtime = SimpleNamespace()
    server.runtime.heartbeat_sandbox = Mock()
    server.runtime.register_sandbox = Mock()
    run_config = RunConfig(
        name="announce",
        description="announce",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
    )

    await server._announce_sandbox(run_config)

    server.runtime.heartbeat_sandbox.assert_called_once()
    server.runtime.register_sandbox.assert_not_called()
