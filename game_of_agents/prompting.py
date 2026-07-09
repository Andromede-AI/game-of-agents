from __future__ import annotations

from typing import Mapping

from game_of_agents.agents.base import AgentContext
from game_of_agents.models import AgentConfig, RunState

class _SafePromptValues(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_prompt_template(template: str, values: Mapping[str, object]) -> str:
    return template.format_map(_SafePromptValues(values)).strip()


def prompt_values_for_run(run: RunState, agent_config: AgentConfig) -> dict[str, object]:
    return {
        "agent_id": agent_config.agent_id,
        "run_name": run.config.name,
        "run_description": run.config.description,
        "agent_goal": agent_config.prompt,
        "duration_minutes": run.config.duration_minutes,
        "settlement_mode": run.config.settlement_mode.value,
        "rating_spread": run.config.rating.matchmaking_spread,
    }


def prompt_values_for_context(
    context: AgentContext,
    agent_config: AgentConfig,
    *,
    last_summary: str = "",
) -> dict[str, object]:
    values = prompt_values_for_run(context.run, agent_config)
    values.update(
        {
            "minutes_left": f"{context.minutes_left:.1f}",
            "best_elo": f"{context.best_elo:.1f}",
            "rank": context.rank,
            "last_summary": last_summary or "No prior step summary.",
        }
    )
    return values
