from __future__ import annotations

import asyncio

from game_of_agents.models import AgentConfig, AgentRuntime, RunState, RunStatus
from game_of_agents.store import RunStore
from tests.config_factories import make_run_config


def test_store_does_not_downgrade_stopping_run_to_running(tmp_path) -> None:
    store = RunStore(tmp_path / "runs")
    run = RunState(
        config=make_run_config(
            name="status-merge",
            description="store should preserve stopping state",
            agents=[AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK)],
        )
    )

    async def scenario() -> None:
        run.status = RunStatus.STOPPING
        await store.save_run(run)

        stale = run.model_copy(deep=True)
        stale.status = RunStatus.RUNNING
        await store.save_run(stale)

        persisted = await store.get_run(run.run_id)
        assert persisted is not None
        assert persisted.status == RunStatus.STOPPING

    asyncio.run(scenario())
