from __future__ import annotations

from pathlib import Path

from game_of_agents.model_resolver import resolve_agent_config
from game_of_agents.models import AgentConfig, AgentRuntime
from game_of_agents.runtime_commands import prepare_runtime_command


def test_resolve_agent_config_builds_codex_command() -> None:
    resolved = resolve_agent_config(AgentConfig(agent_id="alpha", model="gpt-5.4"))

    assert resolved.runtime == AgentRuntime.CODEX
    assert resolved.command[:3] == ["codex", "exec", "--model"]
    assert "--dangerously-bypass-approvals-and-sandbox" in resolved.command
    assert '-c' in resolved.command
    assert 'model_reasoning_effort="medium"' in resolved.command


def test_resolve_agent_config_builds_gemini_command() -> None:
    resolved = resolve_agent_config(AgentConfig(agent_id="alpha", model="gemini-2.5-pro"))

    assert resolved.runtime == AgentRuntime.GEMINI
    assert resolved.command == ["gemini", "--model", "gemini-2.5-pro", "--prompt", "--yolo"]


def test_resolve_agent_config_builds_opencode_command_with_provider_model() -> None:
    resolved = resolve_agent_config(AgentConfig(agent_id="alpha", model="grok-4"))

    assert resolved.runtime == AgentRuntime.OPENCODE
    assert resolved.command == [
        "opencode",
        "run",
        "--model",
        "openrouter/x-ai/grok-4",
        "--format",
        "json",
    ]


def test_resolve_agent_config_routes_deepseek_to_openrouter() -> None:
    resolved = resolve_agent_config(AgentConfig(agent_id="alpha", model="deepseek-r1"))

    assert resolved.runtime == AgentRuntime.OPENCODE
    assert resolved.command == ["opencode", "run", "--model", "openrouter/deepseek/deepseek-r1:free", "--format", "json"]


def test_prepare_codex_command_resumes_latest_session() -> None:
    command = prepare_runtime_command(
        AgentRuntime.CODEX,
        ["codex", "exec", "--model", "gpt-5.4"],
        model="gpt-5.4",
        workspace=Path("/tmp/workspace"),
        started=True,
    )

    assert command[:4] == ["codex", "exec", "resume", "--last"]
    assert "--model" in command
    assert "--json" in command
    assert '-c' in command
    assert 'model_reasoning_effort="medium"' in command


def test_prepare_codex_command_keeps_explicit_reasoning_effort() -> None:
    command = prepare_runtime_command(
        AgentRuntime.CODEX,
        [
            "codex",
            "exec",
            "--model",
            "gpt-5.4",
            "-c",
            'model_reasoning_effort="high"',
        ],
        model="gpt-5.4",
        workspace=Path("/tmp/workspace"),
        started=False,
    )

    assert command.count("-c") == 1
    assert 'model_reasoning_effort="high"' in command
    assert 'model_reasoning_effort="medium"' not in command


def test_prepare_gemini_command_adds_prompt_and_resume_flags() -> None:
    command = prepare_runtime_command(
        AgentRuntime.GEMINI,
        ["gemini", "--model", "gemini-2.5-flash"],
        model="gemini-2.5-flash",
        workspace=Path("/tmp/workspace"),
        started=True,
    )

    assert "--prompt" in command
    assert "--yolo" in command
    assert "--output-format" in command
    assert "stream-json" in command
    assert command[-2:] == ["--resume", "latest"]


def test_prepare_opencode_command_adds_dir_and_continue() -> None:
    workspace = Path("/tmp/workspace")
    command = prepare_runtime_command(
        AgentRuntime.OPENCODE,
        ["opencode", "run", "--model", "openrouter/deepseek/deepseek-chat-v3.1"],
        model="deepseek-r1",
        workspace=workspace,
        started=True,
    )

    assert command[:2] == ["opencode", "run"]
    assert "--format" in command
    assert "json" in command
    assert "--dir" in command
    assert str(workspace) in command
    assert "--continue" in command
