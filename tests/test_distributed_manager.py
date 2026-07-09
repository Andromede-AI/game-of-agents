from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from game_of_agents.distributed_manager import DistributedRunManager
from game_of_agents.models import AgentConfig, AgentRuntime, RunConfig, RunState, RunStatus


def make_run(*, status: RunStatus = RunStatus.RUNNING) -> RunState:
    return RunState(
        run_id="run_test123",
        config=RunConfig(
            name="test",
            description="test",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        ),
        status=status,
    )


@pytest.mark.asyncio
async def test_stop_run_ignores_terminator_spawn_failure_after_requesting_stop() -> None:
    manager = object.__new__(DistributedRunManager)
    manager.runtime = Mock()
    manager.runtime.request_stop = Mock()

    original = make_run(status=RunStatus.RUNNING)
    stopping = make_run(status=RunStatus.STOPPING)
    manager.get_run = AsyncMock(side_effect=[original, stopping])
    manager._spawn_terminator_background = Mock()

    result = await DistributedRunManager.stop_run(manager, original.run_id)

    assert result.status == RunStatus.STOPPING
    manager.runtime.request_stop.assert_called_once()
    manager._spawn_terminator_background.assert_called_once_with(original)


@pytest.mark.asyncio
async def test_stop_run_is_idempotent_for_non_running_states() -> None:
    manager = object.__new__(DistributedRunManager)
    manager.runtime = Mock()
    manager.runtime.request_stop = Mock()

    finished = make_run(status=RunStatus.FINISHED)
    manager.get_run = AsyncMock(return_value=finished)
    manager._spawn_terminator_background = Mock()

    result = await DistributedRunManager.stop_run(manager, finished.run_id)

    assert result.status == RunStatus.FINISHED
    manager.runtime.request_stop.assert_not_called()
    manager._spawn_terminator_background.assert_not_called()


@pytest.mark.asyncio
async def test_get_run_uses_summary_by_default() -> None:
    manager = object.__new__(DistributedRunManager)
    manager.runtime = Mock()
    summary = {
        "runId": "run_test123",
        "name": "test",
        "description": "test",
        "status": "running",
        "createdAt": 0,
        "startedAt": None,
        "finishedAt": None,
        "updatedAt": 0,
        "agentCount": 1,
        "botCount": 0,
        "activeBotCount": 0,
        "gameCount": 0,
        "offerCount": 0,
        "purchaseCount": 0,
        "reviewCount": 0,
        "bestAgentId": None,
        "bestRating": None,
        "config": make_run().config.model_dump(mode="json"),
        "finalScores": {},
        "payouts": {},
    }
    expected = make_run(status=RunStatus.RUNNING)
    manager.runtime.get_run_summary = Mock(return_value=summary)
    manager.runtime.get_run_dashboard = Mock()
    manager.runtime.summary_to_run_state = Mock(return_value=expected)

    result = await DistributedRunManager.get_run(manager, "run_test123")

    assert result is expected
    manager.runtime.get_run_summary.assert_called_once_with("run_test123")
    manager.runtime.get_run_dashboard.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_terminator_background_consumes_task_exception() -> None:
    manager = object.__new__(DistributedRunManager)
    run = make_run()

    async def explode(_: RunState) -> None:
        raise RuntimeError("boom")

    manager._spawn_terminator_sandbox = explode

    DistributedRunManager._spawn_terminator_background(manager, run)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_start_run_ensures_sandboxes_from_config_agents() -> None:
    manager = object.__new__(DistributedRunManager)
    manager.runtime = Mock()
    manager.runtime.start_run = Mock()
    manager.runtime.list_sandboxes = Mock(return_value=[])
    run = RunState(
        run_id="run_test123",
        config=RunConfig(
            name="test",
            description="test",
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        ),
        status=RunStatus.PENDING,
    )
    started = run.model_copy(update={"status": RunStatus.RUNNING})
    manager.get_run = AsyncMock(side_effect=[run, started])
    manager._spawn_tournament_sandbox = AsyncMock()
    manager._spawn_agent_sandbox = AsyncMock()

    result = await DistributedRunManager.start_run(manager, run.run_id)

    assert result.status == RunStatus.RUNNING
    manager.runtime.start_run.assert_called_once_with(run.run_id)
    manager._spawn_tournament_sandbox.assert_awaited_once()
    assert manager._spawn_agent_sandbox.await_count == 2
    manager._spawn_agent_sandbox.assert_any_await(started, "alpha")
    manager._spawn_agent_sandbox.assert_any_await(started, "beta")


@pytest.mark.asyncio
async def test_start_run_repairs_missing_agent_sandboxes_when_already_running() -> None:
    manager = object.__new__(DistributedRunManager)
    manager.runtime = Mock()
    manager.runtime.start_run = Mock()
    manager.runtime.list_sandboxes = Mock(
        return_value=[{"kind": "tournament", "status": "running", "sandboxId": "sb_t"}]
    )
    run = RunState(
        run_id="run_test123",
        config=RunConfig(
            name="test",
            description="test",
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        ),
        status=RunStatus.RUNNING,
    )
    manager.get_run = AsyncMock(side_effect=[run, run])
    manager._spawn_tournament_sandbox = AsyncMock()
    manager._spawn_agent_sandbox = AsyncMock()

    await DistributedRunManager.start_run(manager, run.run_id)

    manager.runtime.start_run.assert_not_called()
    manager._spawn_tournament_sandbox.assert_not_awaited()
    assert manager._spawn_agent_sandbox.await_count == 2
