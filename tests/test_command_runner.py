from __future__ import annotations

import sys
import textwrap
from unittest.mock import AsyncMock

import pytest

from game_of_agents.agents.base import AgentContext
from game_of_agents.agents.command import CommandAgentRunner
from game_of_agents.models import AgentConfig, AgentRuntime, RunConfig, RunState
from game_of_agents.workspaces import WorkspaceManager


UPDATED_BOT = textwrap.dedent(
    """
    from game_of_agents.games.base import BotAction
    from game_of_agents.games.poker.bot import PokerBot, PokerObservation


    class WorkspaceBot(PokerBot):
        def choose_action(self, observation: PokerObservation) -> BotAction:
            if "raise_to" in observation.legal_actions and observation.min_raise_to is not None:
                if observation.to_call <= 2:
                    return BotAction("raise_to", observation.min_raise_to)
            if "check_call" in observation.legal_actions:
                return BotAction("check_call")
            return BotAction("fold")
    """
).strip()


def scaffold_context(tmp_path, command: list[str]) -> AgentContext:
    config = RunConfig(
        name="command-runner",
        description="runner test",
        duration_minutes=1,
        last_warning_minutes=1,
        agents=[
            AgentConfig(
                agent_id="alpha",
                runtime=AgentRuntime.CLAUDE,
                command=command,
                prompt="Improve bot.py",
            )
        ],
    )
    run = RunState(config=config)
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    agent = workspaces.scaffold_agent(run, config.agents[0])
    run.agents[agent.agent_id] = agent
    return AgentContext(run=run, agent=agent, minutes_left=1.0, best_elo=1000.0, rank=1)


@pytest.mark.asyncio
async def test_command_runner_returns_submission_when_bot_changes(tmp_path) -> None:
    script = (
        "from pathlib import Path;"
        f"Path('bot.py').write_text({UPDATED_BOT!r}, encoding='utf-8')"
    )
    context = scaffold_context(tmp_path, [sys.executable, "-c", script])

    submission = await CommandAgentRunner("claude").step(context)

    assert submission is not None
    assert submission.entrypoint == "WorkspaceBot"
    assert submission.module_path == "bot.py"
    assert submission.artifacts[0].content == UPDATED_BOT


@pytest.mark.asyncio
async def test_command_runner_skips_submission_when_bot_does_not_change(tmp_path) -> None:
    context = scaffold_context(tmp_path, [sys.executable, "-c", "print('no-op')"])

    submission = await CommandAgentRunner("claude").step(context)

    assert submission is None


@pytest.mark.asyncio
async def test_command_runner_uses_custom_prompt_templates(tmp_path) -> None:
    script = (
        "from pathlib import Path;"
        "Path('prompt.txt').write_text(__import__('sys').argv[1], encoding='utf-8')"
    )
    context = scaffold_context(tmp_path, [sys.executable, "-c", script])
    context.run.config.agents[0].initial_prompt_template = (
        "agent={agent_id};run={run_name};goal={agent_goal};minutes={minutes_left}"
    )

    submission = await CommandAgentRunner("claude").step(context)
    prompt_text = (tmp_path / "workspaces" / context.run.run_id / context.agent.agent_id / "prompt.txt").read_text(
        encoding="utf-8"
    )

    assert submission is None
    assert "agent=alpha" in prompt_text
    assert "run=command-runner" in prompt_text
    assert "goal=Improve bot.py" in prompt_text


@pytest.mark.asyncio
async def test_command_runner_closes_stdin_for_child_processes(tmp_path) -> None:
    script = (
        "import sys; from pathlib import Path; "
        "sys.stdin.read(); "
        f"Path('bot.py').write_text({UPDATED_BOT!r}, encoding='utf-8')"
    )
    context = scaffold_context(tmp_path, [sys.executable, "-c", script])

    submission = await CommandAgentRunner("opencode").step(context)

    assert submission is not None
    assert submission.artifacts[0].content == UPDATED_BOT


@pytest.mark.asyncio
async def test_command_runner_streams_output_chunks(tmp_path) -> None:
    script = textwrap.dedent(
        """
        import sys
        import time

        print("hello from stdout", flush=True)
        time.sleep(0.05)
        print("hello from stderr", file=sys.stderr, flush=True)
        """
    ).strip()
    events: list[tuple[str, dict[str, object]]] = []
    updates: list[dict[str, object]] = []
    context = scaffold_context(tmp_path, [sys.executable, "-c", script])
    context.step_id = "step_test"
    context.report_event = lambda kind, payload: _record_event(events, kind, payload)
    context.update_agent_state = lambda payload: _record_update(updates, payload)

    submission = await CommandAgentRunner("claude").step(context)

    assert submission is None
    assert any(kind == "agent.output.chunk" and "hello from stdout" in str(payload["chunk"]) for kind, payload in events)
    assert any(kind == "agent.output.chunk" and "hello from stderr" in str(payload["chunk"]) for kind, payload in events)
    assert all(payload["step_id"] == "step_test" for kind, payload in events if kind == "agent.output.chunk")
    assert any("last_output_at" in payload for payload in updates)


@pytest.mark.asyncio
async def test_command_runner_emits_structured_claude_blocks(tmp_path) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    context = scaffold_context(tmp_path, [sys.executable, "-c", "print('ok')"])
    context.step_id = "step_structured"
    context.report_event = lambda kind, payload: _record_event(events, kind, payload)
    runner = CommandAgentRunner("claude")

    await runner._on_output(
        context,
        "stdout",
        '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","name":"shell","input":{"cmd":"ls"}}}\n',
    )
    await runner._on_output(
        context,
        "stdout",
        '{"type":"content_block_delta","index":1,"delta":{"text":"hello world"}}\n',
    )
    await runner._on_output(
        context,
        "stdout",
        '{"type":"content_block_stop","index":1}\n',
    )

    structured = [payload for kind, payload in events if kind == "agent.output.block"]
    assert any(payload["block_kind"] == "tool" and payload["action"] == "start" for payload in structured)
    assert any(payload["block_kind"] == "text" and payload["action"] == "append" for payload in structured)
    assert any(payload["action"] == "stop" for payload in structured)


def test_command_runner_timeout_uses_remaining_run_budget(tmp_path) -> None:
    context = scaffold_context(tmp_path, [sys.executable, "-c", "print('ok')"])
    context.minutes_left = 12.0

    runner = CommandAgentRunner("claude")

    assert runner._timeout_seconds(context) == 720.0


def test_command_runner_can_still_use_explicit_timeout_override(tmp_path) -> None:
    context = scaffold_context(tmp_path, [sys.executable, "-c", "print('ok')"])
    context.minutes_left = 12.0

    runner = CommandAgentRunner("claude", default_timeout_seconds=30)

    assert runner._timeout_seconds(context) == 30.0


def test_command_runner_strips_pty_ansi_sequences() -> None:
    runner = CommandAgentRunner("claude")

    cleaned = runner._strip_ansi("OK\r\n\u001b[?25h\u001b]0;\u0007")

    assert cleaned == "OK\n"


def test_command_runner_prepares_workspace_with_volume_hooks(tmp_path) -> None:
    context = scaffold_context(tmp_path, [sys.executable, "-c", "print('ok')"])
    calls: list[str] = []
    runner = CommandAgentRunner(
        "claude",
        read_hook=lambda: calls.append("read"),
        write_hook=lambda: calls.append("write"),
    )

    runner._prepare_workspace(tmp_path / "workspaces" / context.run.run_id / context.agent.agent_id)

    assert calls == ["read", "write"]


def test_command_runner_maps_modal_workspace_paths(tmp_path, monkeypatch) -> None:
    runner = CommandAgentRunner("claude")
    monkeypatch.setattr("game_of_agents.agents.command.settings.data_dir", tmp_path / "goa_data")
    target = tmp_path / "goa_data" / "workspaces" / "run_123" / "alpha"
    target.mkdir(parents=True)

    mapped = runner._workspace_path("/__modal/volumes/vo-xyz/workspaces/run_123/alpha")

    assert mapped == target


@pytest.mark.asyncio
async def test_command_runner_bootstraps_codex_login_with_workspace_local_home(tmp_path, monkeypatch) -> None:
    context = scaffold_context(tmp_path, ["codex", "exec", "--model", "gpt-5.4"])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    runner = CommandAgentRunner("codex")
    login = AsyncMock()
    monkeypatch.setattr(runner, "_codex_login_local", login)

    env = await runner._ensure_local_runtime_auth(
        context,
        tmp_path / "workspaces" / context.run.run_id / context.agent.agent_id,
        ["codex", "exec", "--model", "gpt-5.4"],
    )

    assert env is not None
    assert env["HOME"].endswith(".codex-home")
    assert env["CODEX_HOME"].endswith(".codex-home")
    login.assert_awaited_once()


async def _record_event(events: list[tuple[str, dict[str, object]]], kind: str, payload: dict[str, object]) -> None:
    events.append((kind, payload))


async def _record_update(updates: list[dict[str, object]], payload: dict[str, object]) -> None:
    updates.append(payload)
