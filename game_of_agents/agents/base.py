from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from game_of_agents.models import AgentState, BotArtifact, BotSubmissionRequest, RunState


AgentEventReporter = Callable[[str, dict[str, object]], Awaitable[None]]
AgentStateUpdater = Callable[[dict[str, object]], Awaitable[None]]


@dataclass
class AgentContext:
    run: RunState
    agent: AgentState
    minutes_left: float
    best_elo: float
    rank: int
    step_id: str | None = None
    last_warning: bool = False
    report_event: AgentEventReporter | None = None
    update_agent_state: AgentStateUpdater | None = None


class AgentRunner(abc.ABC):
    @abc.abstractmethod
    async def step(self, context: AgentContext) -> BotSubmissionRequest | None:
        raise NotImplementedError

    @abc.abstractmethod
    async def continue_message(self, context: AgentContext) -> str:
        raise NotImplementedError

    def last_full_output(self, context: AgentContext) -> str | None:
        """Return the full uncompressed output of the most recent step, if available."""
        return None

    def last_prompt(self, context: AgentContext) -> str | None:
        """Return the full prompt used for the most recent step, if available."""
        return None

    def step_prompt(self, context: AgentContext) -> str | None:
        """Return the next prompt that should be sent for this step, if available."""
        return None

    def last_summary(self, context: AgentContext) -> str | None:
        """Return a short summary of the most recent step, if available."""
        return None

    async def shutdown_run(self, run: RunState) -> None:
        """Clean up any per-run runtime resources."""
