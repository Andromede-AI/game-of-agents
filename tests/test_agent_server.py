from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from game_of_agents.agent_server import (
    AgentServer,
    BlockBuffer,
    MAX_LOG_BLOCK_CHARS,
    MAX_RAW_STREAM_BLOCK_CHARS,
    RAW_STREAM_TRUNCATED_MARKER,
)
from game_of_agents.models import AgentConfig, AgentRuntime, RunConfig


def make_server() -> AgentServer:
    server = object.__new__(AgentServer)
    server.run_id = "run_test"
    server.agent_id = "alpha"
    server.sandbox_id = "sb_test"
    server.block_buffer = BlockBuffer()
    server.runtime = SimpleNamespace()
    server.session_runtime = server.runtime
    server.dashboard_runtime = server.runtime
    server.submission_runtime = server.runtime
    server.heartbeat_runtime = server.runtime
    server.log_runtime = server.runtime
    server.comment_runtime = server.runtime
    server.last_summary = ""
    server.started = False
    server.workspace = Path("/tmp/goa/run_test/alpha")
    server._ship_task = None
    server._diagnostics_task = None
    server._heartbeat_helper = None
    now = "2026-03-17T00:00:00+00:00"
    server._diagnostics_state = {
        "started_at": now,
        "last_progress_at": now,
        "last_progress_reason": "startup",
        "last_prompt_at": None,
        "last_visible_output_at": None,
        "last_step_started_at": None,
        "last_step_completed_at": None,
        "last_step_runtime": None,
        "last_step_exit_code": None,
        "last_submission_at": None,
        "last_submission_id": None,
        "active_process": None,
        "scopes": {
            "main": {"phase": "startup", "operation": None, "since": now, "last_success_at": None, "last_error": None, "last_error_at": None, "failure_count": 0},
            "comment": {"phase": "idle", "operation": None, "since": now, "last_success_at": None, "last_error": None, "last_error_at": None, "failure_count": 0},
            "logs": {"phase": "idle", "operation": None, "since": now, "last_success_at": None, "last_error": None, "last_error_at": None, "failure_count": 0},
            "heartbeat": {"phase": "running", "operation": None, "since": now, "last_success_at": None, "last_error": None, "last_error_at": None, "failure_count": 0},
        },
        "recent_failures": [],
    }
    server._new_runtime_client = lambda: server.runtime
    return server


def make_run_config() -> RunConfig:
    return RunConfig(
        name="agent-server",
        description="tests",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
    )


@pytest.mark.asyncio
async def test_flush_blocks_requeues_on_failure() -> None:
    server = make_server()
    calls: list[list[dict[str, object]]] = []

    def append_log_blocks(run_id: str, *, agent_id: str, blocks: list[dict[str, object]]) -> None:
        calls.append(list(blocks))
        if len(calls) == 1:
            raise RuntimeError("temporary write failure")

    server.runtime.append_log_blocks = append_log_blocks
    server._upsert_block(
        block_id="step:text",
        step_id="step",
        role="assistant",
        kind="text",
        title="Response",
        text="hello",
        collapsed=False,
        streaming=False,
    )

    with pytest.raises(RuntimeError):
        await server._flush_blocks()

    assert len(server.block_buffer.pending) == 1

    await server._flush_blocks()

    assert len(calls) == 2
    assert server.block_buffer.pending == []


def test_append_to_block_rolls_over_large_output() -> None:
    server = make_server()
    server._upsert_block(
        block_id="step:text",
        step_id="step",
        role="assistant",
        kind="text",
        title="Response",
        text="",
        collapsed=False,
        streaming=True,
    )

    server._append_to_block("step:text", "x" * (MAX_LOG_BLOCK_CHARS + 32), streaming=True)

    first = server.block_buffer.blocks["step:text"]
    second = server.block_buffer.blocks["step:text:part2"]
    assert len(first["text"]) == MAX_LOG_BLOCK_CHARS
    assert len(second["text"]) == 32
    assert second["title"] == "Response (cont. 2)"


def test_append_raw_stream_chunk_captures_non_line_buffered_output() -> None:
    server = make_server()

    server._append_raw_stream_chunk("step", "stdout", '{"type":"delta"')
    server._append_raw_stream_chunk("step", "stdout", ',"text":"hi"}')

    block = server.block_buffer.blocks["step:raw:stdout"]
    assert block["title"] == "Raw Stdout"
    assert block["collapsed"] is True
    assert block["text"] == '{"type":"delta","text":"hi"}'


def test_append_raw_stream_chunk_truncates_large_stream() -> None:
    server = make_server()

    server._append_raw_stream_chunk("step", "stderr", "x" * (MAX_RAW_STREAM_BLOCK_CHARS + 128))

    block = server.block_buffer.blocks["step:raw:stderr"]
    assert block["title"] == "Raw Stderr"
    assert block["text"].endswith(RAW_STREAM_TRUNCATED_MARKER)
    assert len(block["text"]) <= MAX_RAW_STREAM_BLOCK_CHARS + len(RAW_STREAM_TRUNCATED_MARKER)


@pytest.mark.asyncio
async def test_restore_session_state_ignores_system_autosubmit_blocks() -> None:
    server = make_server()
    autosubmit = Mock(
        text="Auto-submitted bot.py as sub_123 (revision 2).",
        title="Auto Submission",
        role="system",
        kind="summary",
    )
    response = Mock(
        text="I changed the bot logic and validated the workspace.",
        title="Response",
        role="assistant",
        kind="text",
    )
    server.dashboard_runtime.get_agent_conversation = Mock(return_value=[autosubmit, response])

    await server._restore_session_state_from_conversation()

    assert server.started is True
    assert server.last_summary == "I changed the bot logic and validated the workspace."


@pytest.mark.asyncio
async def test_ship_logs_loop_retries_after_flush_error(monkeypatch) -> None:
    server = make_server()
    server._flush_blocks = AsyncMock(side_effect=[RuntimeError("boom"), None, asyncio.CancelledError()])
    sleep_calls = 0

    async def fake_sleep(_: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr("game_of_agents.agent_server.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await server._ship_logs_loop()

    assert server._flush_blocks.await_count >= 2


@pytest.mark.asyncio
async def test_heartbeat_loop_retries_after_exception(monkeypatch) -> None:
    server = make_server()
    calls = 0

    def heartbeat_sandbox(run_id: str, *, sandbox_id: str, status: str, heartbeat_ttl_seconds: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")

    server.runtime.heartbeat_sandbox = heartbeat_sandbox
    sleep_calls = 0

    async def fake_sleep(_: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr("game_of_agents.agent_server.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await server._heartbeat_loop(make_run_config())

    assert calls == 2


@pytest.mark.asyncio
async def test_call_runtime_recreates_client_after_timeout(monkeypatch) -> None:
    server = make_server()
    server.heartbeat_runtime.heartbeat_sandbox = Mock()
    original = server.heartbeat_runtime
    replacement = SimpleNamespace(heartbeat_sandbox=Mock())
    server._new_runtime_client = Mock(return_value=replacement)

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        raise TimeoutError("hung")

    monkeypatch.setattr("game_of_agents.agent_server.asyncio.wait_for", fake_wait_for)

    with pytest.raises(TimeoutError):
        await server._call_runtime("heartbeat_runtime", "heartbeat_sandbox", "run_test", timeout=1.0)

    assert server.heartbeat_runtime is replacement
    assert server.heartbeat_runtime is not original
    assert server._diagnostics_state["scopes"]["heartbeat"]["failure_count"] == 1
    assert server._diagnostics_state["recent_failures"]


@pytest.mark.asyncio
async def test_diagnostics_loop_publishes_metadata(monkeypatch) -> None:
    server = make_server()
    calls: list[dict[str, object]] = []

    def heartbeat_sandbox(run_id: str, *, sandbox_id: str, status: str, metadata_patch: dict[str, object], heartbeat_ttl_seconds: int) -> None:
        calls.append(
            {
                "run_id": run_id,
                "sandbox_id": sandbox_id,
                "status": status,
                "metadata_patch": metadata_patch,
                "heartbeat_ttl_seconds": heartbeat_ttl_seconds,
            }
        )

    server.heartbeat_runtime.heartbeat_sandbox = heartbeat_sandbox
    sleep_calls = 0

    async def fake_sleep(_: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr("game_of_agents.agent_server.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await server._diagnostics_loop(make_run_config())

    assert len(calls) == 1
    diagnostics = calls[0]["metadata_patch"]["diagnostics"]  # type: ignore[index]
    assert diagnostics["last_progress_reason"] == "startup"
    assert diagnostics["scopes"]["main"]["phase"] == "startup"


@pytest.mark.asyncio
async def test_claim_operator_steers_records_block_and_flushes() -> None:
    server = make_server()
    server.runtime.claim_pending_agent_steers = Mock(
        return_value=[{"steer_id": "steer_123", "text": "Tighten preflop ranges."}]
    )
    server._flush_blocks = AsyncMock()

    steers = await server._claim_operator_steers()

    assert steers == ["Tighten preflop ranges."]
    assert server.block_buffer.blocks["steer:steer_123"]["title"] == "Operator Steer"
    server._flush_blocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_announce_sandbox_prefers_heartbeat_over_register() -> None:
    server = make_server()
    server.runtime.heartbeat_sandbox = Mock()
    server.runtime.register_sandbox = Mock()

    await server._announce_sandbox(make_run_config())

    server.runtime.heartbeat_sandbox.assert_called_once()
    server.runtime.register_sandbox.assert_not_called()


@pytest.mark.asyncio
async def test_announce_sandbox_falls_back_to_register() -> None:
    server = make_server()
    server.runtime.heartbeat_sandbox = Mock(side_effect=RuntimeError("missing"))
    server.runtime.register_sandbox = Mock()

    await server._announce_sandbox(make_run_config())

    server.runtime.register_sandbox.assert_called_once()


def test_prompt_includes_operator_steering_note() -> None:
    server = make_server()
    config = make_run_config()
    agent_config = config.agents[0]

    prompt = server._prompt(
        config,
        agent_config,
        minutes_left=12.5,
        rank=2,
        total_agents=6,
        projected_rank=1,
        best_rating=12.34,
        projected_payout=14.2,
        equity_delta=1.86,
        runtime_notice=None,
        operator_steers=["Sell less junk and fix your river sizing."],
        warning=False,
    )

    assert "Operator steering note:" in prompt
    assert "Sell less junk and fix your river sizing." in prompt
    assert "Projected payout rank: #1/6" in prompt


def test_restore_session_state_marks_started_from_existing_activity() -> None:
    server = make_server()

    server._restore_session_state(
        {
            "run": {
                "state": {
                    "agents": {
                        "alpha": {
                            "agent_id": "alpha",
                            "last_message": "Keep iterating on preflop play.",
                            "last_activity_at": "2026-03-16T20:00:00Z",
                        }
                    }
                }
            }
        }
    )

    assert server.started is True
    assert server.last_summary == "Keep iterating on preflop play."


def test_restore_session_state_does_not_treat_registration_activity_as_resume() -> None:
    server = make_server()

    server._restore_session_state(
        {
            "run": {
                "state": {
                    "agents": {
                        "alpha": {
                            "agent_id": "alpha",
                            "last_activity_at": "2026-03-16T20:00:00Z",
                            "last_message": None,
                            "current_step_started_at": None,
                            "last_output_at": None,
                        }
                    }
                }
            }
        }
    )

    assert server.started is False


def test_handle_structured_event_ignores_assistant_wrapper_messages() -> None:
    server = make_server()
    server._upsert_block(
        block_id="step:prompt",
        step_id="step",
        role="user",
        kind="prompt",
        title="Prompt",
        text="hi",
        collapsed=True,
        streaming=False,
    )

    handled = server._handle_structured_event(
        "step",
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "big wrapper payload"}],
                },
            }
        ),
    )

    assert handled is True
    assert list(server.block_buffer.blocks) == ["step:prompt"]
