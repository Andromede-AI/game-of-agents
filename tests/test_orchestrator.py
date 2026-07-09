from __future__ import annotations

import sys
import textwrap
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from game_of_agents.models import (
    AgentConfig,
    AgentRuntime,
    CommentFeedConfig,
    CommentFeedRuntime,
    RunConfig,
    RunStatus,
)
from game_of_agents.orchestrator import Orchestrator
from game_of_agents.settings import settings
from tests.config_factories import make_run_config


@pytest.mark.asyncio
async def test_short_run_finishes_with_games_and_scores(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    run = await orchestrator.create_run(
        make_run_config(
            name="orchestrator-smoke",
            description="Short test run",
            concurrent_matches=1,
            duration_minutes=1,
            last_warning_minutes=1,
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        )
    )

    finished = await orchestrator.run_iterations(run.run_id, 3)

    assert finished.status == RunStatus.FINISHED
    assert finished.bots
    assert finished.games
    assert finished.final_scores
    assert finished.payouts
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_finalize_run_preserves_comment_feed_messages(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-comments"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    run = await orchestrator.create_run(
        make_run_config(
            name="comment-finalize",
            description="Final comment sidecar should persist",
            concurrent_matches=1,
            duration_minutes=1,
            last_warning_minutes=1,
            comment_feed=CommentFeedConfig(enabled=True, runtime=CommentFeedRuntime.MOCK),
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        )
    )

    finished = await orchestrator.run_iterations(run.run_id, 2)

    assert finished.status == RunStatus.FINISHED
    assert finished.comments
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_mock_runner_state_is_isolated_per_run(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-2"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()

    async def launch_once(name: str):
        run = await orchestrator.create_run(
            make_run_config(
                name=name,
                description="repeat agent ids",
                concurrent_matches=1,
                duration_minutes=1,
                last_warning_minutes=1,
                agents=[
                    AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                    AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
                ],
            )
        )
        return await orchestrator.run_iterations(run.run_id, 2)

    first = await launch_once("first")
    second = await launch_once("second")

    assert first.bots
    assert second.bots
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_command_runner_submits_bots_through_orchestrator(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-3"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    bot_code = textwrap.dedent(
        """
        from game_of_agents.games.base import BotAction
        from game_of_agents.games.poker.bot import PokerBot, PokerObservation


        class WorkspaceBot(PokerBot):
            def choose_action(self, observation: PokerObservation) -> BotAction:
                if "check_call" in observation.legal_actions:
                    return BotAction("check_call")
                return BotAction("fold")
        """
    ).strip()
    script = (
        "from pathlib import Path;"
        f"Path('bot.py').write_text({bot_code!r}, encoding='utf-8')"
    )

    run = await orchestrator.create_run(
        make_run_config(
            name="command-runner-smoke",
            description="short command runner test",
            concurrent_matches=1,
            duration_minutes=1,
            last_warning_minutes=1,
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.CLAUDE, command=[sys.executable, "-c", script]),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.CLAUDE, command=[sys.executable, "-c", script]),
            ],
        )
    )

    finished = await orchestrator.run_iterations(run.run_id, 2)

    assert finished.status == RunStatus.FINISHED
    assert finished.bots
    assert finished.games
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_start_run_submits_bootstrap_bots(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-4"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    run = await orchestrator.create_run(
        make_run_config(
            name="bootstrap-bots",
            description="seed bots on start",
            concurrent_matches=1,
            duration_minutes=1,
            last_warning_minutes=1,
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        )
    )

    started = await orchestrator.start_run(run.run_id)

    assert started.status == RunStatus.RUNNING
    refreshed = await orchestrator.get_run(run.run_id)
    assert len(refreshed.bots) == 2
    assert all(agent.best_bot_id for agent in refreshed.agents.values())
    await orchestrator.stop_run(run.run_id)
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_refresh_live_run_state_tolerates_recent_controller_lookup_errors(tmp_path, monkeypatch) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-5"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    run = await orchestrator.create_run(
        make_run_config(
            name="refresh-live-state",
            description="controller lookup tolerance",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        )
    )
    run.status = RunStatus.RUNNING
    run.controller_sandbox_id = "sb-missing"
    run.controller_status = "running"
    run.controller_last_seen_at = datetime.now(tz=UTC)
    await orchestrator.store.save_run(run)

    monkeypatch.setattr("game_of_agents.orchestrator.running_inside_modal", lambda: True)
    monkeypatch.setattr("game_of_agents.orchestrator.in_controller_sandbox", lambda: False)
    monkeypatch.setattr(
        orchestrator,
        "_controller_sandbox_handle",
        AsyncMock(side_effect=RuntimeError("Sandbox not found")),
    )

    refreshed = await orchestrator.get_run(run.run_id)

    assert refreshed.status == RunStatus.RUNNING
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_touch_controller_does_not_overwrite_stopping_status(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-6"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    run = await orchestrator.create_run(
        make_run_config(
            name="controller-touch",
            description="controller heartbeat should preserve stop",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        )
    )
    run.status = RunStatus.STOPPING
    await orchestrator.store.save_run(run)

    touched = await orchestrator._touch_controller(run.run_id, sandbox_id="sb-test")

    assert touched.status == RunStatus.STOPPING
    assert touched.controller_sandbox_id == "sb-test"
    assert touched.controller_status == "running"
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


@pytest.mark.asyncio
async def test_refresh_live_run_state_finalizes_stopping_run_when_controller_exits(tmp_path, monkeypatch) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "goa-data-7"
    settings.convex_url = None
    settings.convex_deploy_key = None
    orchestrator = Orchestrator()
    run = await orchestrator.create_run(
        make_run_config(
            name="refresh-stop-finish",
            description="stopping run should finalize when controller exits",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        )
    )
    run.status = RunStatus.STOPPING
    await orchestrator.store.save_run(run)

    class _Sandbox:
        class poll:
            @staticmethod
            async def aio():
                return 0

    monkeypatch.setattr("game_of_agents.orchestrator.running_inside_modal", lambda: True)
    monkeypatch.setattr("game_of_agents.orchestrator.in_controller_sandbox", lambda: False)
    monkeypatch.setattr(orchestrator, "_controller_sandbox_handle", AsyncMock(return_value=_Sandbox()))
    finalized = run.model_copy(deep=True)
    finalized.status = RunStatus.FINISHED
    monkeypatch.setattr(orchestrator, "finalize_run", AsyncMock(return_value=finalized))

    refreshed = await orchestrator.get_run(run.run_id)

    assert refreshed.status == RunStatus.FINISHED
    orchestrator.finalize_run.assert_awaited_once_with(run.run_id)
    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key
