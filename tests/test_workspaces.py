from __future__ import annotations

import game_of_agents.workspaces as workspaces_module
from game_of_agents.models import AgentConfig, AgentRuntime, RunConfig, RunState
from game_of_agents.workspaces import WorkspaceManager


def test_workspace_scaffold_uses_custom_templates(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    config = RunConfig(
        name="workspace-prompts",
        description="workspace prompt test",
        agents=[
            AgentConfig(
                agent_id="alpha",
                runtime=AgentRuntime.CLAUDE,
                prompt="Play carefully",
                workspace_readme_template="README {agent_id} {run_name} {agent_goal}",
                workspace_rules_template="RULES {agent_id} {run_description} {settlement_mode}",
            )
        ],
    )
    run = RunState(config=config)

    agent_state = manager.scaffold_agent(run, config.agents[0])
    root = manager.agent_root(run.run_id, agent_state.agent_id)

    assert (root / "README_AGENT.md").read_text(encoding="utf-8") == "README alpha workspace-prompts Play carefully"
    assert (root / "RULES.txt").read_text(encoding="utf-8") == "RULES alpha workspace prompt test net"
    assert agent_state.sandbox_name == f"goa-agent-{run.run_id}-alpha"


def test_workspace_scaffold_includes_poker_guides(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    config = RunConfig(
        name="workspace-guides",
        description="workspace guide test",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.CLAUDE)],
    )
    run = RunState(config=config)

    agent_state = manager.scaffold_agent(run, config.agents[0])
    root = manager.agent_root(run.run_id, agent_state.agent_id)

    pokerkit_guide = (root / "POKERKIT_GUIDE.md").read_text(encoding="utf-8")
    runtime_guide = (root / "POKER_RUNTIME.md").read_text(encoding="utf-8")

    assert "PokerKit" in pokerkit_guide
    assert "WorkspaceBot" in runtime_guide
    assert "choose_action" in runtime_guide
    tools_guide = (root / "TOOLS.md").read_text(encoding="utf-8")
    assert "--description" in tools_guide
    assert "--evidence" in tools_guide


def test_workspace_scaffold_respects_channel_flags_in_tools_guide(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    config = RunConfig(
        name="workspace-channel-flags",
        description="workspace channel flags test",
        marketplace_enabled=False,
        chat_enabled=True,
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.CLAUDE)],
    )
    run = RunState(config=config)

    agent_state = manager.scaffold_agent(run, config.agents[0])
    root = manager.agent_root(run.run_id, agent_state.agent_id)
    tools_guide = (root / "TOOLS.md").read_text(encoding="utf-8")

    assert "Marketplace:" not in tools_guide
    assert "Chat:" in tools_guide


def test_run_config_rejects_comment_feed_when_chat_disabled() -> None:
    try:
        RunConfig(
            name="workspace-invalid-chat",
            description="workspace invalid chat test",
            chat_enabled=False,
            comment_feed={"enabled": True},
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.CLAUDE)],
        )
    except ValueError as exc:
        assert "chat_enabled" in str(exc)
    else:
        raise AssertionError("expected comment_feed/chat_enabled validation error")


def test_workspace_manager_runs_sync_hooks(tmp_path) -> None:
    calls: list[str] = []
    manager = WorkspaceManager(
        tmp_path / "workspaces",
        read_hook=lambda: calls.append("read"),
        write_hook=lambda: calls.append("write"),
    )
    config = RunConfig(
        name="workspace-hooks",
        description="workspace hook test",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.CLAUDE)],
    )
    run = RunState(config=config)

    agent_state = manager.scaffold_agent(run, config.agents[0])
    artifacts = manager.bot_artifacts(agent_state.workspace, ["bot.py"])

    assert artifacts
    assert calls == ["write", "read"]


def test_workspace_scaffold_falls_back_when_vendored_docs_are_missing(tmp_path, monkeypatch) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces")
    monkeypatch.setattr(workspaces_module, "VENDORED_POKERKIT_DOCS_DIR", tmp_path / "missing-docs")
    config = RunConfig(
        name="workspace-fallback-docs",
        description="workspace fallback docs test",
        agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.CLAUDE)],
    )
    run = RunState(config=config)

    agent_state = manager.scaffold_agent(run, config.agents[0])
    root = manager.agent_root(run.run_id, agent_state.agent_id)

    fallback_index = root / "POKERKIT_DOCS" / "LOCAL_INDEX.md"
    assert fallback_index.exists()
    assert "POKERKIT_GUIDE.md" in fallback_index.read_text(encoding="utf-8")
