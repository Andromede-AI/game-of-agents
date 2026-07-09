"""Resolve a model ID to a runtime and CLI command."""

from __future__ import annotations

from game_of_agents.models import AgentConfig, AgentRuntime
from game_of_agents.runtime_commands import default_runtime_command

# ── Well-known models ─────────────────────────────────────────────────
# Anthropic
ANTHROPIC_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

# OpenAI
OPENAI_MODELS = [
    "gpt-5.4",
    "o3",
    "o4-mini",
]

# Google
GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
]

# xAI
GROK_MODELS = [
    "grok-4",
    "grok-3",
]

# Chinese / other open-code models
OPENCODE_MODELS = [
    "deepseek-r1",
    "deepseek-v3",
    "qwen-3-coder",
    "qwen-3-235b",
]

ALL_KNOWN_MODELS = (
    ANTHROPIC_MODELS + OPENAI_MODELS + GEMINI_MODELS + GROK_MODELS + OPENCODE_MODELS
)


def _detect_runtime(model: str) -> AgentRuntime:
    """Detect the CLI runtime from a model identifier."""
    m = model.lower()
    if m.startswith("claude"):
        return AgentRuntime.CLAUDE
    if m.startswith(("gpt", "o1", "o3", "o4")):
        return AgentRuntime.CODEX
    if m.startswith("gemini"):
        return AgentRuntime.GEMINI
    # Everything else goes through opencode
    return AgentRuntime.OPENCODE


def _build_command(runtime: AgentRuntime, model: str) -> list[str]:
    """Build the default CLI command for a given runtime and model."""
    return default_runtime_command(runtime, model)


def resolve_agent_config(agent: AgentConfig) -> AgentConfig:
    """If *model* is set, auto-fill *runtime* and *command* when they are at defaults."""
    if not agent.model:
        return agent

    runtime = _detect_runtime(agent.model)
    command = _build_command(runtime, agent.model)

    # Only override if the user didn't explicitly set runtime/command
    updates: dict = {}
    if agent.runtime == AgentRuntime.MOCK:
        updates["runtime"] = runtime
    if not agent.command:
        updates["command"] = command

    if updates:
        return agent.model_copy(update=updates)
    return agent
