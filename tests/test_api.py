from __future__ import annotations

from fastapi.testclient import TestClient

from game_of_agents.api import create_app
from game_of_agents.config_defaults import default_prompt_value
from game_of_agents.models import RunConfig
from game_of_agents.settings import settings


def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.api_token}"}


def test_api_can_create_and_drain_run(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "api-data"
    settings.convex_url = None
    settings.convex_deploy_key = None
    client = TestClient(create_app())

    create_response = client.post(
        "/runs",
        headers=auth_headers(),
        json={
            "name": "api-smoke",
            "description": "api run",
            "duration_minutes": 1,
            "last_warning_minutes": 1,
            "concurrent_matches": 1,
            "match_executor": "thread",
            "max_active_bots_per_agent": 3,
            "elo_spread": 150,
            "settlement_mode": "net",
            "game_time_bank_seconds": 2,
            "action_increment_seconds": 0,
            "agents": [
                {"agent_id": "alpha", "runtime": "mock", "internet_access": False},
                {"agent_id": "beta", "runtime": "mock", "internet_access": False},
            ],
        },
    )
    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]

    drain_response = client.post(f"/runs/{run_id}/drain?iterations=2", headers=auth_headers())
    assert drain_response.status_code == 200
    payload = drain_response.json()
    assert payload["status"] == "finished"

    leaderboard_response = client.get(f"/runs/{run_id}/leaderboard", headers=auth_headers())
    assert leaderboard_response.status_code == 200
    leaderboard = leaderboard_response.json()
    assert leaderboard["agents"]
    assert leaderboard["bots"]

    analysis_response = client.get(f"/runs/{run_id}/analysis", headers=auth_headers())
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    assert analysis["runId"] == run_id
    assert "giniCoefficient" in analysis
    assert "avgAggression" in analysis
    assert "marketplace" in analysis

    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


def test_api_can_delete_finished_run(tmp_path) -> None:
    original_data_dir = settings.data_dir
    original_convex_url = settings.convex_url
    original_convex_key = settings.convex_deploy_key
    settings.data_dir = tmp_path / "api-delete-data"
    settings.convex_url = None
    settings.convex_deploy_key = None
    client = TestClient(create_app())

    create_response = client.post(
        "/runs",
        headers=auth_headers(),
        json={
            "name": "delete-smoke",
            "description": "delete me",
            "duration_minutes": 1,
            "last_warning_minutes": 1,
            "concurrent_matches": 1,
            "match_executor": "thread",
            "max_active_bots_per_agent": 3,
            "elo_spread": 150,
            "settlement_mode": "net",
            "game_time_bank_seconds": 2,
            "action_increment_seconds": 0,
            "agents": [
                {"agent_id": "alpha", "runtime": "mock", "internet_access": False},
                {"agent_id": "beta", "runtime": "mock", "internet_access": False},
            ],
        },
    )
    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]

    drain_response = client.post(f"/runs/{run_id}/drain?iterations=1", headers=auth_headers())
    assert drain_response.status_code == 200

    delete_response = client.delete(f"/runs/{run_id}", headers=auth_headers())
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "deleted"}

    get_response = client.get(f"/runs/{run_id}", headers=auth_headers())
    assert get_response.status_code == 404

    settings.data_dir = original_data_dir
    settings.convex_url = original_convex_url
    settings.convex_deploy_key = original_convex_key


def test_run_config_defaults_prompt_text_from_yaml() -> None:
    config = RunConfig.model_validate(
        {
            "name": "yaml-defaults",
            "description": "defaults smoke",
            "agents": [
                {"agent_id": "alpha", "runtime": "mock", "internet_access": False},
                {"agent_id": "beta", "runtime": "mock", "internet_access": False},
            ],
        }
    )

    assert config.initial_prompt_template == default_prompt_value("initial_prompt_template")
    assert config.continue_prompt_template == default_prompt_value("continue_prompt_template")
    assert config.warning_prompt_template == default_prompt_value("warning_prompt_template")
    assert config.workspace_readme_template == default_prompt_value("workspace_readme_template")
    assert config.workspace_rules_template == default_prompt_value("workspace_rules_template")
    assert config.pokerkit_guide == default_prompt_value("pokerkit_guide")
    assert config.poker_runtime_guide == default_prompt_value("poker_runtime_guide")
