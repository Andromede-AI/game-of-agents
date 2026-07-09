from __future__ import annotations

from pathlib import Path

import typer
import uvicorn
import yaml

from game_of_agents.api import create_app
from game_of_agents.logging import configure_logging
from game_of_agents.models import RunConfig
from game_of_agents.orchestrator import Orchestrator
from game_of_agents.settings import settings

app = typer.Typer(no_args_is_help=True)


@app.command()
def serve() -> None:
    configure_logging()
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
    )


@app.command()
def run(config: Path) -> None:
    configure_logging()
    payload = yaml.safe_load(config.read_text())
    run_config = RunConfig.model_validate(payload)
    orchestrator = Orchestrator()
    state = orchestrator.create_run_sync(run_config)
    orchestrator.run_once_sync(state.run_id)
    typer.echo(state.run_id)
