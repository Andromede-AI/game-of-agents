from __future__ import annotations

import asyncio
import os
import subprocess

from game_of_agents.logging import configure_logging
from game_of_agents.modal_runtime import CONTROLLER_ROLE, SANDBOX_ROLE_ENV
from game_of_agents.orchestrator import Orchestrator


def _sync_data_volume() -> None:
    mountpoint = os.environ.get("DATA_DIR", "/goa_data")
    subprocess.run(["sync", mountpoint], check=False)


async def run_controller(run_id: str) -> None:
    configure_logging()
    orchestrator = Orchestrator(write_hook=_sync_data_volume)
    controller_sandbox_id = os.environ.get("MODAL_SANDBOX_ID")
    if controller_sandbox_id:
        await orchestrator._touch_controller(run_id, sandbox_id=controller_sandbox_id)
    try:
        await orchestrator.run_to_completion(run_id)
    except Exception as exc:
        await orchestrator.mark_run_failed(run_id, str(exc))
        raise


def main() -> None:
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m game_of_agents.sandbox_controller <run_id>")
    os.environ[SANDBOX_ROLE_ENV] = CONTROLLER_ROLE
    asyncio.run(run_controller(sys.argv[1]))


if __name__ == "__main__":
    main()
