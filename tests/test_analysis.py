"""Tests for the analysis pipeline against synthetic run data."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from analysis.loader import load_run, load_events, RunData
from analysis.metrics import (
    compute_agent_stats,
    compute_pairwise_stats,
    compute_marketplace_stats,
    aggression_by_street,
)


def _make_run_state(
    *,
    n_agents: int = 4,
    n_games: int = 10,
    n_offers: int = 2,
    n_purchases: int = 1,
    include_actions: bool = True,
    heterogeneous: bool = True,
) -> dict:
    """Build a minimal synthetic RunState dict."""
    agents = {}
    agent_configs = []
    for i in range(n_agents):
        aid = f"agent-{i}"
        model = "claude-sonnet" if (i < n_agents // 2 or not heterogeneous) else "gemini-pro"
        agents[aid] = {
            "agent_id": aid,
            "runtime": "claude" if "claude" in model else "gemini",
            "internet_access": False,
            "workspace": f"/tmp/ws/{aid}",
            "best_elo": 1000.0 + i * 50,
            "best_rating_mu": 25.0 + i,
            "best_rating_sigma": 8.0,
            "best_bot_id": f"bot-{aid}-0",
            "status": "finished",
        }
        agent_configs.append({"agent_id": aid, "model": model, "runtime": agents[aid]["runtime"]})

    bots = {}
    for i in range(n_agents):
        aid = f"agent-{i}"
        bid = f"bot-{aid}-0"
        bots[bid] = {
            "bot_id": bid,
            "agent_id": aid,
            "name": f"bot-{i}",
            "elo": 1000.0 + i * 50,
            "rating_mu": 25.0 + i,
            "rating_sigma": 8.0,
            "matches_played": n_games // 2,
            "failure_count": 0,
            "active": True,
            "created_at": "2026-04-07T00:00:00Z",
        }

    games = {}
    agent_ids = [f"agent-{i}" for i in range(n_agents)]
    bot_ids = [f"bot-agent-{i}-0" for i in range(n_agents)]
    for g in range(n_games):
        gid = f"game-{g}"
        a_idx = g % n_agents
        b_idx = (g + 1) % n_agents
        winner_idx = a_idx if g % 3 != 0 else b_idx  # agent a wins 2/3

        actions = []
        if include_actions:
            for r in range(3):  # 3 rounds
                actions.append({"type": "round_start", "round_index": r + 1, "active_seats": [0, 1]})
                # Preflop actions
                actions.append({"kind": "raise_to", "amount": 20, "round_index": r + 1,
                                "seat": 0, "local_seat": 0, "active_seats": [0, 1], "time_remaining": 2.5})
                actions.append({"kind": "check_call", "amount": 20, "round_index": r + 1,
                                "seat": 1, "local_seat": 1, "active_seats": [0, 1], "time_remaining": 2.3})
                # Flop
                actions.append({"type": "deal_board", "cards": ["2h", "3d", "Ks"], "street": 0,
                                "round_index": r + 1, "active_seats": [0, 1]})
                actions.append({"kind": "check_call", "amount": 0, "round_index": r + 1,
                                "seat": 0, "local_seat": 0, "active_seats": [0, 1], "time_remaining": 2.0})
                actions.append({"kind": "raise_to", "amount": 40, "round_index": r + 1,
                                "seat": 1, "local_seat": 1, "active_seats": [0, 1], "time_remaining": 1.8})
                actions.append({"kind": "fold", "round_index": r + 1,
                                "seat": 0, "local_seat": 0, "active_seats": [0, 1], "time_remaining": 1.5})

        games[gid] = {
            "game_id": gid,
            "run_id": "run-test",
            "status": "finished",
            "table_size": 2,
            "round_count": 3,
            "participants": [
                {"bot_id": bot_ids[a_idx], "agent_id": agent_ids[a_idx], "seat": 0, "placement": 1 if winner_idx == a_idx else 2, "ending_chips": 250, "eliminated_round": None},
                {"bot_id": bot_ids[b_idx], "agent_id": agent_ids[b_idx], "seat": 1, "placement": 1 if winner_idx == b_idx else 2, "ending_chips": 150, "eliminated_round": None},
            ],
            "winner_bot_id": bot_ids[winner_idx],
            "reason": None,
            "actions": actions,
            "started_at": "2026-04-07T00:00:00Z",
            "finished_at": "2026-04-07T00:01:00Z",
            "duration_seconds": 60.0,
        }

    offers = {}
    for i in range(n_offers):
        oid = f"offer-{i}"
        offers[oid] = {
            "offer_id": oid,
            "seller_agent_id": agent_ids[i % n_agents],
            "bot_id": bot_ids[i % n_agents],
            "title": f"Strategy {i}",
            "description": "Good code",
            "evidence": "High rating",
            "price_pct": 5.0 + i,
            "artifact_paths": ["bot.py"],
            "review_count": 0,
            "created_at": "2026-04-07T00:00:00Z",
        }

    purchases = {}
    for i in range(n_purchases):
        pid = f"purchase-{i}"
        buyer_idx = (i + 1) % n_agents
        seller_idx = i % n_agents
        purchases[pid] = {
            "purchase_id": pid,
            "offer_id": f"offer-{i}",
            "buyer_agent_id": agent_ids[buyer_idx],
            "seller_agent_id": agent_ids[seller_idx],
            "price_pct": 5.0,
            "created_at": "2026-04-07T00:00:00Z",
        }

    comments = {
        "cmt-0": {
            "message_id": "cmt-0",
            "author_agent_id": agent_ids[0],
            "commentator_id": f"{agent_ids[0]}-commentator",
            "text": "I'm winning!",
            "sequence": 1,
            "parent_message_id": None,
            "created_at": "2026-04-07T00:00:00Z",
        }
    }

    return {
        "run_id": "run-test",
        "config": {
            "name": "test-run",
            "description": "test",
            "agents": agent_configs,
            "duration_minutes": 10,
        },
        "status": "finished",
        "created_at": "2026-04-07T00:00:00Z",
        "agents": agents,
        "bots": bots,
        "games": games,
        "offers": offers,
        "purchases": purchases,
        "reviews": {},
        "comments": comments,
        "transcripts": {},
        "final_scores": {f"agent-{i}": 1000.0 + i * 50 for i in range(n_agents)},
        "payouts": {f"agent-{i}": 1000.0 + i * 50 for i in range(n_agents)},
    }


@pytest.fixture
def run_file(tmp_path: Path) -> Path:
    state = _make_run_state()
    path = tmp_path / "run-test.json"
    path.write_text(json.dumps(state))
    return path


@pytest.fixture
def run_data(run_file: Path) -> RunData:
    return load_run(run_file)


def test_load_run(run_data: RunData):
    assert run_data.run_id == "run-test"
    assert len(run_data.agents) == 4
    assert len(run_data.bots) == 4
    assert len(run_data.games) == 10
    assert len(run_data.finished_games) == 10
    assert len(run_data.offers) == 2
    assert len(run_data.purchases) == 1
    assert len(run_data.comments) == 1


def test_agent_model(run_data: RunData):
    assert run_data.agent_model("agent-0") == "claude-sonnet"
    assert run_data.agent_model("agent-2") == "gemini-pro"


def test_game_actions_parsed(run_data: RunData):
    game = run_data.finished_games[0]
    actions = game.player_actions
    assert len(actions) > 0
    kinds = {a.kind for a in actions}
    assert kinds == {"raise_to", "check_call", "fold"}
    assert all(a.agent_id is not None for a in actions)


def test_actions_by_street(run_data: RunData):
    game = run_data.finished_games[0]
    by_street = game.actions_by_street()
    assert "preflop" in by_street
    assert "flop" in by_street
    assert len(by_street["preflop"]) > 0  # raise + call before flop
    assert len(by_street["flop"]) > 0  # check + raise + fold on flop


def test_agent_stats(run_data: RunData):
    stats = compute_agent_stats(run_data)
    assert len(stats) == 4
    for aid, s in stats.items():
        assert s.games_played > 0
        assert s.total_actions > 0
        assert s.aggression_factor >= 0
        assert 0 <= s.win_rate <= 1


def test_pairwise_stats(run_data: RunData):
    pairwise = compute_pairwise_stats(run_data)
    assert len(pairwise) > 0
    for ps in pairwise:
        assert ps.games_together > 0
        assert ps.a_raises + ps.a_checks_calls + ps.a_folds > 0


def test_marketplace_stats(run_data: RunData):
    ms = compute_marketplace_stats(run_data)
    assert ms.total_offers == 2
    assert ms.total_purchases == 1
    assert ms.avg_price_pct > 0
    # With heterogeneous agents, one purchase should be cross-model
    assert ms.same_model_purchases + ms.cross_model_purchases == 1


def test_marketplace_ingroup_bias(tmp_path: Path):
    """Test in-group bias detection with controlled purchases."""
    state = _make_run_state(n_agents=4, n_purchases=0, heterogeneous=True)
    # Add same-model purchase (agent-0 claude buys from agent-1 claude)
    state["purchases"]["p1"] = {
        "purchase_id": "p1", "offer_id": "offer-0",
        "buyer_agent_id": "agent-0", "seller_agent_id": "agent-1",
        "price_pct": 5.0, "created_at": "2026-04-07T00:00:00Z",
    }
    # Add cross-model purchase (agent-0 claude buys from agent-2 gemini)
    state["purchases"]["p2"] = {
        "purchase_id": "p2", "offer_id": "offer-1",
        "buyer_agent_id": "agent-0", "seller_agent_id": "agent-2",
        "price_pct": 5.0, "created_at": "2026-04-07T00:00:00Z",
    }
    path = tmp_path / "ingroup.json"
    path.write_text(json.dumps(state))
    run = load_run(path)
    ms = compute_marketplace_stats(run)
    assert ms.same_model_purchases == 1
    assert ms.cross_model_purchases == 1


def test_aggression_by_street(run_data: RunData):
    streets = aggression_by_street(run_data, "agent-0")
    assert "preflop" in streets
    assert "flop" in streets
    assert "turn" in streets
    assert "river" in streets
    # In our synthetic data: preflop has raise+call, flop has check+raise+fold
    assert streets["preflop"] > 0  # agent-0 at seat 0 raises preflop


def test_load_events(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        '{"event_id":"e1","run_id":"run-test","kind":"game.finished","payload":{},"created_at":"2026-04-07T00:00:00Z"}\n'
        '{"event_id":"e2","run_id":"run-test","kind":"bot.submitted","payload":{},"created_at":"2026-04-07T00:01:00Z"}\n'
    )
    events = load_events(events_file)
    assert len(events) == 2
    assert events[0].kind == "game.finished"
    assert events[1].kind == "bot.submitted"


def test_empty_run(tmp_path: Path):
    state = _make_run_state(n_games=0, n_offers=0, n_purchases=0)
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(state))
    run = load_run(path)
    stats = compute_agent_stats(run)
    assert all(s.games_played == 0 for s in stats.values())
    assert all(s.aggression_factor == 0 for s in stats.values())
    ms = compute_marketplace_stats(run)
    assert ms.total_offers == 0
