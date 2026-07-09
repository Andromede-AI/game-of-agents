from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import modal

from game_of_agents.convex_runtime import ConvexRuntimeClient
from game_of_agents.modal_runtime import app_secret, image, lookup_app
from game_of_agents.models import RunConfig
from game_of_agents.settings import settings


async def spawn_tournament_sandbox(
    run_id: str,
    run_config: RunConfig,
    runtime: ConvexRuntimeClient,
    *,
    restart_count: int = 0,
    replaced_sandbox_id: str | None = None,
) -> str:
    app = await lookup_app()
    suffix = "" if restart_count <= 0 else f"-r{restart_count}"
    metadata: dict[str, Any] = {"restart_count": restart_count}
    if replaced_sandbox_id:
        metadata["replaced_sandbox_id"] = replaced_sandbox_id
    sandbox = await modal.Sandbox.create.aio(
        "python",
        "-m",
        "game_of_agents.tournament_server",
        run_id,
        app=app,
        image=image,
        secrets=[app_secret],
        timeout=max(900, (run_config.duration_minutes + run_config.convergence_tail_minutes) * 60 + 1800),
        idle_timeout=max(600, run_config.duration_minutes * 60 + 900),
        workdir="/root",
        cpu=run_config.tournament_cpu,
        memory=4096,
        env={
            "GOA_RUN_ID": run_id,
            "DATA_DIR": settings.data_dir.as_posix(),
        },
        name=f"goa-tournament-{run_id}{suffix}",
    )
    await asyncio.to_thread(
        runtime.register_sandbox,
        run_id,
        role="tournament",
        sandbox_id=sandbox.object_id,
        status="running",
        metadata=metadata,
        heartbeat_ttl_seconds=max(30, int(max(1.0, run_config.tournament_poll_seconds) * 10)),
    )
    return str(sandbox.object_id)


async def spawn_agent_sandbox(
    run_id: str,
    run_config: RunConfig,
    runtime: ConvexRuntimeClient,
    *,
    agent_id: str,
    restart_count: int = 0,
    replaced_sandbox_id: str | None = None,
) -> str:
    app = await lookup_app()
    suffix = "" if restart_count <= 0 else f"-r{restart_count}"
    sandbox_name = f"goa-agent-{run_id}-{agent_id}{suffix}"
    metadata: dict[str, Any] = {"name": sandbox_name, "restart_count": restart_count}
    if replaced_sandbox_id:
        metadata["replaced_sandbox_id"] = replaced_sandbox_id
    sandbox = await modal.Sandbox.create.aio(
        "python",
        "-m",
        "game_of_agents.agent_server",
        run_id,
        agent_id,
        app=app,
        image=image,
        secrets=[app_secret],
        timeout=max(900, run_config.duration_minutes * 60 + 900),
        idle_timeout=max(600, run_config.duration_minutes * 60 + 300),
        workdir="/root",
        cpu=run_config.agent_sandbox_cpu,
        memory=2048,
        env={
            "GOA_RUN_ID": run_id,
            "GOA_AGENT_ID": agent_id,
        },
        name=sandbox_name,
    )
    await asyncio.to_thread(
        runtime.register_sandbox,
        run_id,
        role="agent",
        sandbox_id=sandbox.object_id,
        agent_id=agent_id,
        status="running",
        metadata=metadata,
        heartbeat_ttl_seconds=max(120, int(max(1.0, run_config.agent_poll_seconds) * 10)),
    )
    return str(sandbox.object_id)


async def spawn_terminator_sandbox(run_id: str, *, grace_seconds: int) -> None:
    app = await lookup_app()
    await modal.Sandbox.create.aio(
        "python",
        "-m",
        "game_of_agents.sandbox_terminator",
        run_id,
        str(grace_seconds),
        app=app,
        image=image,
        secrets=[app_secret],
        timeout=max(60, grace_seconds + 60),
        idle_timeout=max(60, grace_seconds + 30),
        workdir="/root",
        cpu=1,
        memory=512,
        name=f"goa-terminator-{run_id}-{int(datetime.now(tz=UTC).timestamp())}",
    )


async def terminate_sandbox(sandbox_id: str) -> bool:
    sandbox = await modal.Sandbox.from_id.aio(str(sandbox_id))
    exit_code = await sandbox.poll.aio()
    if exit_code is None:
        await sandbox.terminate.aio()
        return True
    return False
