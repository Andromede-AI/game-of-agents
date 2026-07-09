from __future__ import annotations

from pathlib import Path

import pytest

from game_of_agents.comment_feed import CommentFeedService
from game_of_agents.events import JsonlEventSink
from game_of_agents.models import (
    AgentConfig,
    AgentRuntime,
    CommentFeedConfig,
    CommentFeedRuntime,
    CommentPostRequest,
    ConversationTurn,
    ConversationTurnKind,
    RunConfig,
    RunState,
)
from game_of_agents.store import RunStore
from game_of_agents.workspaces import WorkspaceManager
from tests.config_factories import make_run_config


@pytest.mark.asyncio
async def test_comment_feed_posts_and_lists_messages(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    events = JsonlEventSink(tmp_path / "events")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    run = RunState(
        config=make_run_config(
            name="comments",
            description="feed test",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        )
    )
    run.agents["alpha"] = workspaces.scaffold_agent(run, run.config.agents[0])
    await store.save_run(run)
    service = CommentFeedService(store, events)

    message = await service.post_message(
        run.run_id,
        CommentPostRequest(
            author_agent_id="alpha",
            commentator_id="alpha-commentator",
            text="hello world",
        ),
    )
    listed = await service.list_messages(run.run_id)

    assert listed == [message]
    assert listed[0].sequence == 1


@pytest.mark.asyncio
async def test_comment_sidecar_mock_can_reply_with_transcript_context(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    events = JsonlEventSink(tmp_path / "events")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    run = RunState(
        config=make_run_config(
            name="comments",
            description="feed sidecar test",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
            comment_feed=CommentFeedConfig(enabled=True, runtime=CommentFeedRuntime.MOCK),
        )
    )
    run.agents["alpha"] = workspaces.scaffold_agent(run, run.config.agents[0])
    await store.save_run(run)
    service = CommentFeedService(store, events)

    await service.record_turn(
        run.run_id,
        ConversationTurn(
            run_id=run.run_id,
            agent_id="alpha",
            kind=ConversationTurnKind.RESPONSE,
            text="I tightened the preflop range.",
        ),
    )
    reply_target = await service.post_message(
        run.run_id,
        CommentPostRequest(
            author_agent_id="alpha",
            commentator_id="other-commentator",
            text="What changed?",
        ),
    )
    run = await store.get_run(run.run_id)
    assert run is not None

    created = await service.maybe_run_sidecars(run)

    assert len(created) == 1
    assert created[0].parent_message_id == reply_target.message_id
    assert created[0].text.lower().startswith("i")


@pytest.mark.asyncio
async def test_comment_sidecar_mock_reflects_agent_personality(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    events = JsonlEventSink(tmp_path / "events")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    run = RunState(
        config=make_run_config(
            name="comments",
            description="personality feed sidecar test",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK, prompt="Aggressive pressure player")],
            comment_feed=CommentFeedConfig(enabled=True, runtime=CommentFeedRuntime.MOCK),
        )
    )
    run.agents["alpha"] = workspaces.scaffold_agent(run, run.config.agents[0])
    await store.save_run(run)
    service = CommentFeedService(store, events)

    await service.record_turn(
        run.run_id,
        ConversationTurn(
            run_id=run.run_id,
            agent_id="alpha",
            kind=ConversationTurnKind.RESPONSE,
            text="I widened the three-bet ranges on the button.",
        ),
    )
    run = await store.get_run(run.run_id)
    assert run is not None

    created = await service.maybe_run_sidecars(run)

    assert len(created) == 1
    assert "knives out" in created[0].text.lower()
    assert "i" in created[0].text.lower()


def test_comment_feed_config_accepts_second_level_cadence() -> None:
    config = CommentFeedConfig(enabled=True, interval_seconds=30)

    assert config.cadence_seconds == 30
