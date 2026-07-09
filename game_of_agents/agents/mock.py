from __future__ import annotations

from pathlib import Path

from game_of_agents.agents.base import AgentContext, AgentRunner
from game_of_agents.models import BotArtifact, BotSubmissionRequest
from game_of_agents.prompting import (
    prompt_values_for_context,
    render_prompt_template,
)


class MockAgentRunner(AgentRunner):
    def __init__(self) -> None:
        self._submission_count: dict[tuple[str, str], int] = {}
        self._last_summary: dict[tuple[str, str], str] = {}

    async def step(self, context: AgentContext) -> BotSubmissionRequest | None:
        key = (context.run.run_id, context.agent.agent_id)
        count = self._submission_count.get(key, 0) + 1
        self._submission_count[key] = count
        if count > 2:
            self._last_summary[key] = "No new bot revision this step."
            return None

        workspace = Path(context.agent.workspace)
        strategy_path = workspace / f"bot_v{count}.py"
        aggressiveness = 2 + count
        strategy_path.write_text(
            "\n".join(
                [
                    "from game_of_agents.games.base import BotAction",
                    "from game_of_agents.games.poker.bot import PokerBot, PokerObservation",
                    "",
                    "",
                    "class GeneratedBot(PokerBot):",
                    "    def choose_action(self, observation: PokerObservation) -> BotAction:",
                    f"        threshold = {aggressiveness}",
                    "        if 'raise_to' in observation.legal_actions and observation.min_raise_to is not None:",
                    "            if observation.to_call <= threshold:",
                    "                return BotAction('raise_to', observation.min_raise_to)",
                    "        if 'check_call' in observation.legal_actions:",
                    "            return BotAction('check_call')",
                    "        return BotAction('fold')",
                ]
            ),
            encoding="utf-8",
        )
        self._last_summary[key] = (
            f"Submitted mock bot revision {count} with aggressiveness threshold {aggressiveness}."
        )
        return BotSubmissionRequest(
            agent_id=context.agent.agent_id,
            name=f"{context.agent.agent_id}-bot-{count}",
            description=f"Auto-generated mock strategy version {count}",
            entrypoint="GeneratedBot",
            module_path=str(strategy_path.relative_to(workspace)),
            artifacts=[BotArtifact(path=str(strategy_path.relative_to(workspace)), content=strategy_path.read_text(encoding='utf-8'))],
        )

    async def continue_message(self, context: AgentContext) -> str:
        agent_config = self._agent_config(context)
        template = (
            agent_config.warning_prompt_template or context.run.config.warning_prompt_template
            if context.last_warning
            else agent_config.continue_prompt_template or context.run.config.continue_prompt_template
        )
        return render_prompt_template(template, prompt_values_for_context(context, agent_config))

    def step_prompt(self, context: AgentContext) -> str | None:
        agent_config = self._agent_config(context)
        key = (context.run.run_id, context.agent.agent_id)
        template = (
            agent_config.initial_prompt_template or context.run.config.initial_prompt_template
            if self._submission_count.get(key, 0) == 0
            else agent_config.warning_prompt_template or context.run.config.warning_prompt_template
            if context.last_warning
            else agent_config.continue_prompt_template or context.run.config.continue_prompt_template
        )
        return render_prompt_template(
            template,
            prompt_values_for_context(context, agent_config),
        )

    def last_summary(self, context: AgentContext) -> str | None:
        return self._last_summary.get((context.run.run_id, context.agent.agent_id))

    def _agent_config(self, context: AgentContext):
        agent_config = next(
            agent for agent in context.run.config.agents if agent.agent_id == context.agent.agent_id
        )
        return agent_config
