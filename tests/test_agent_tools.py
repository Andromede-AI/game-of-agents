from __future__ import annotations

import pytest
import typer

from game_of_agents.agent_tools import _synthesized_offer_metadata
from game_of_agents import agent_tools


def test_synthesized_offer_metadata_uses_provided_text() -> None:
    analytics = {
        "self": {
            "agent_id": "alpha",
            "best_rating": 12.5,
            "best_bot_id": "bot_1",
            "tournament_rank": 1,
            "projected_rank": 1,
            "projected_payout": 12.5,
            "equity_delta": 0.0,
        },
        "total_agents": 1,
    }

    description, evidence = _synthesized_offer_metadata(
        analytics,
        agent_id="alpha",
        title="Strong evaluator",
        file_paths=["bot.py"],
        description="Real description",
        evidence="Real evidence",
    )

    assert description == "Real description"
    assert evidence == "Real evidence"


def test_synthesized_offer_metadata_generates_defaults_from_run_state() -> None:
    analytics = {
        "self": {
            "agent_id": "alpha",
            "best_rating": 12.5,
            "best_bot_id": "bot_1",
            "tournament_rank": 1,
            "projected_rank": 1,
            "projected_payout": 13.75,
            "equity_delta": 1.25,
        },
        "total_agents": 2,
    }

    description, evidence = _synthesized_offer_metadata(
        analytics,
        agent_id="alpha",
        title="Strong evaluator",
        file_paths=["bot.py", "helper.py"],
        description="",
        evidence="",
    )

    assert "Strong evaluator" in description
    assert "bot.py, helper.py" in description
    assert "rank #1/2" in evidence
    assert "12.50" in evidence
    assert "bot_1" in evidence
    assert "13.75" in evidence


def test_marketplace_tools_can_be_gated_by_run_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_tools,
        "_run_summary",
        lambda runtime, run_id: {"config": {"marketplace_enabled": False}},
    )

    with pytest.raises(typer.BadParameter, match="marketplace is disabled"):
        agent_tools._require_marketplace_enabled(object(), "run_123")


def test_chat_tools_can_be_gated_by_run_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_tools,
        "_run_summary",
        lambda runtime, run_id: {"config": {"chat_enabled": False}},
    )

    with pytest.raises(typer.BadParameter, match="chat is disabled"):
        agent_tools._require_chat_enabled(object(), "run_123")
