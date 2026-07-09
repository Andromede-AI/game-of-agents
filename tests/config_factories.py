from __future__ import annotations

from typing import Any

from game_of_agents.models import RunConfig


DEFAULT_INITIAL_PROMPT_TEMPLATE = """You are agent {agent_id} in run {run_name}.
Run goal: {run_description}
Agent personality: {agent_goal}
Time left: {minutes_left} minutes."""

DEFAULT_CONTINUE_PROMPT_TEMPLATE = """Continue working on ./bot.py for agent {agent_id}.
Time left: {minutes_left} minutes.
Best rating: {best_elo}.
Leaderboard rank: #{rank}.
Last step summary: {last_summary}"""

DEFAULT_WARNING_PROMPT_TEMPLATE = """Final warning for agent {agent_id} in run {run_name}.
You have {minutes_left} minutes left.
Best rating: {best_elo}.
Leaderboard rank: #{rank}.
Last step summary: {last_summary}"""

DEFAULT_WORKSPACE_README_TEMPLATE = """# Agent {agent_id}

Run: {run_name}
Run goal: {run_description}
Agent personality: {agent_goal}
Duration minutes: {duration_minutes}
Settlement mode: {settlement_mode}
Rating spread: {rating_spread}"""

DEFAULT_WORKSPACE_RULES_TEMPLATE = """You are competing in a multiplayer NLHE tournament.
Read ./POKER_RUNTIME.md for the required bot API and ./POKERKIT_GUIDE.md for the engine library context.
Your personality: {agent_goal}"""

DEFAULT_POKERKIT_GUIDE = """# PokerKit Guide

This project uses PokerKit as the underlying poker engine library.

Official docs:
- https://pokerkit.readthedocs.io/en/"""

DEFAULT_POKER_RUNTIME_GUIDE = """# Poker Runtime Guide

Entrypoint:
- Keep the class name `WorkspaceBot`.
- `WorkspaceBot` must subclass `PokerBot`.
- Required method: `choose_action(self, observation: PokerObservation) -> BotAction`."""


def make_run_config(**overrides: Any) -> RunConfig:
    payload: dict[str, Any] = {
        "name": "test-run",
        "description": "test config",
        "duration_minutes": 1,
        "last_warning_minutes": 1,
        "settlement_mode": "net",
        "max_active_bots_per_agent": 3,
        "concurrent_matches": 1,
        "match_executor": "thread",
        "initial_prompt_template": DEFAULT_INITIAL_PROMPT_TEMPLATE,
        "continue_prompt_template": DEFAULT_CONTINUE_PROMPT_TEMPLATE,
        "warning_prompt_template": DEFAULT_WARNING_PROMPT_TEMPLATE,
        "workspace_readme_template": DEFAULT_WORKSPACE_README_TEMPLATE,
        "workspace_rules_template": DEFAULT_WORKSPACE_RULES_TEMPLATE,
        "pokerkit_guide": DEFAULT_POKERKIT_GUIDE,
        "poker_runtime_guide": DEFAULT_POKER_RUNTIME_GUIDE,
        "agents": [],
    }
    payload.update(overrides)
    return RunConfig.model_validate(payload)
