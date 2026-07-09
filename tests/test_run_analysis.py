from __future__ import annotations

import pytest

from game_of_agents.models import RunState
from game_of_agents.run_analysis import compute_run_analysis_summary


def _make_run_state() -> RunState:
    return RunState.model_validate(
        {
            "run_id": "run_analysis",
            "status": "finished",
            "created_at": "2026-04-07T00:00:00Z",
            "started_at": "2026-04-07T00:05:00Z",
            "finished_at": "2026-04-07T01:05:00Z",
            "config": {
                "name": "analysis-smoke",
                "description": "analysis",
                "agents": [
                    {"agent_id": "agent-0", "runtime": "claude", "model": "claude-sonnet"},
                    {"agent_id": "agent-1", "runtime": "claude", "model": "claude-sonnet"},
                    {"agent_id": "agent-2", "runtime": "gemini", "model": "gemini-pro"},
                    {"agent_id": "agent-3", "runtime": "gemini", "model": "gemini-pro"},
                ],
            },
            "agents": {
                "agent-0": {
                    "agent_id": "agent-0",
                    "runtime": "claude",
                    "internet_access": False,
                    "workspace": "/tmp/agent-0",
                    "best_bot_id": "bot-0",
                    "best_elo": 1000,
                    "status": "finished",
                },
                "agent-1": {
                    "agent_id": "agent-1",
                    "runtime": "claude",
                    "internet_access": False,
                    "workspace": "/tmp/agent-1",
                    "best_bot_id": "bot-1",
                    "best_elo": 1000,
                    "status": "finished",
                },
                "agent-2": {
                    "agent_id": "agent-2",
                    "runtime": "gemini",
                    "internet_access": False,
                    "workspace": "/tmp/agent-2",
                    "best_bot_id": "bot-2",
                    "best_elo": 1000,
                    "status": "finished",
                },
                "agent-3": {
                    "agent_id": "agent-3",
                    "runtime": "gemini",
                    "internet_access": False,
                    "workspace": "/tmp/agent-3",
                    "best_bot_id": "bot-3",
                    "best_elo": 1300,
                    "status": "finished",
                },
            },
            "bots": {
                "bot-0": {"bot_id": "bot-0", "agent_id": "agent-0", "name": "A", "description": "", "entrypoint": "WorkspaceBot", "module_path": "bot.py"},
                "bot-1": {"bot_id": "bot-1", "agent_id": "agent-1", "name": "B", "description": "", "entrypoint": "WorkspaceBot", "module_path": "bot.py"},
                "bot-2": {"bot_id": "bot-2", "agent_id": "agent-2", "name": "C", "description": "", "entrypoint": "WorkspaceBot", "module_path": "bot.py"},
                "bot-3": {"bot_id": "bot-3", "agent_id": "agent-3", "name": "D", "description": "", "entrypoint": "WorkspaceBot", "module_path": "bot.py"},
            },
            "games": {
                "game-0": {
                    "game_id": "game-0",
                    "run_id": "run_analysis",
                    "status": "finished",
                    "participants": [
                        {"bot_id": "bot-0", "agent_id": "agent-0", "seat": 0, "placement": 2, "ending_chips": 0},
                        {"bot_id": "bot-1", "agent_id": "agent-1", "seat": 1, "placement": 1, "ending_chips": 200},
                    ],
                    "winner_bot_id": "bot-1",
                    "actions": [
                        {"kind": "raise_to", "seat": 0, "round_index": 1, "amount": 20},
                        {"kind": "check_call", "seat": 1, "round_index": 1, "amount": 20},
                        {"kind": "check_call", "seat": 0, "round_index": 1, "amount": 0},
                        {"kind": "raise_to", "seat": 1, "round_index": 1, "amount": 40},
                        {"kind": "fold", "seat": 0, "round_index": 1},
                    ],
                },
                "game-1": {
                    "game_id": "game-1",
                    "run_id": "run_analysis",
                    "status": "finished",
                    "participants": [
                        {"bot_id": "bot-2", "agent_id": "agent-2", "seat": 0, "placement": 1, "ending_chips": 200},
                        {"bot_id": "bot-3", "agent_id": "agent-3", "seat": 1, "placement": 2, "ending_chips": 0},
                    ],
                    "winner_bot_id": "bot-2",
                    "actions": [
                        {"kind": "raise_to", "seat": 0, "round_index": 1, "amount": 20},
                        {"kind": "fold", "seat": 1, "round_index": 1},
                    ],
                },
            },
            "offers": {
                "offer-0": {
                    "offer_id": "offer-0",
                    "seller_agent_id": "agent-0",
                    "bot_id": "bot-0",
                    "title": "Preflop bundle",
                    "description": "desc",
                    "evidence": "proof",
                    "price_pct": 5,
                    "artifact_paths": ["bot.py"],
                },
                "offer-1": {
                    "offer_id": "offer-1",
                    "seller_agent_id": "agent-0",
                    "bot_id": "bot-0",
                    "title": "River bundle",
                    "description": "desc",
                    "evidence": "proof",
                    "price_pct": 10,
                    "artifact_paths": ["bot.py"],
                },
                "offer-2": {
                    "offer_id": "offer-2",
                    "seller_agent_id": "agent-2",
                    "bot_id": "bot-2",
                    "title": "Flop bundle",
                    "description": "desc",
                    "evidence": "proof",
                    "price_pct": 15,
                    "artifact_paths": ["bot.py"],
                },
            },
            "purchases": {
                "purchase-0": {
                    "purchase_id": "purchase-0",
                    "offer_id": "offer-0",
                    "buyer_agent_id": "agent-1",
                    "seller_agent_id": "agent-0",
                    "price_pct": 5,
                },
                "purchase-1": {
                    "purchase_id": "purchase-1",
                    "offer_id": "offer-1",
                    "buyer_agent_id": "agent-2",
                    "seller_agent_id": "agent-0",
                    "price_pct": 10,
                },
            },
            "reviews": {
                "review-0": {
                    "review_id": "review-0",
                    "offer_id": "offer-0",
                    "buyer_agent_id": "agent-1",
                    "text": "useful",
                }
            },
            "comments": {
                "comment-0": {
                    "message_id": "comment-0",
                    "run_id": "run_analysis",
                    "author_agent_id": "agent-0",
                    "commentator_id": "agent-0-commentator",
                    "text": "Selling a bundle",
                },
                "comment-1": {
                    "message_id": "comment-1",
                    "run_id": "run_analysis",
                    "author_agent_id": "agent-2",
                    "commentator_id": "agent-2-commentator",
                    "text": "Buying that offer",
                },
            },
        }
    )


def test_compute_run_analysis_summary_returns_expected_metrics() -> None:
    summary = compute_run_analysis_summary(_make_run_state())

    assert summary.runId == "run_analysis"
    assert summary.status == "finished"
    assert summary.botsSubmitted == 4
    assert summary.chatMessageCount == 2
    assert summary.topAgentElo == 1300
    assert summary.giniCoefficient == pytest.approx(0.05232558, rel=1e-5)
    assert summary.avgAggression == pytest.approx(0.375, rel=1e-6)
    assert summary.marketplace.totalOffers == 3
    assert summary.marketplace.totalPurchases == 2
    assert summary.marketplace.totalReviews == 1
    assert summary.marketplace.avgPricePct == pytest.approx(10.0, rel=1e-6)
    assert summary.marketplace.mostActiveSeller == "agent-0"
    assert summary.marketplace.mostActiveBuyer in {"agent-1", "agent-2"}
    assert summary.marketplace.sameModelPurchasePct == pytest.approx(50.0, rel=1e-6)
