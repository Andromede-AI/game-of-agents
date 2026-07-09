from __future__ import annotations

from datetime import UTC, datetime

import pytest

from game_of_agents.convex_runtime import (
    MAX_LOG_BLOCK_TEXT_CHARS,
    ConvexRuntimeClient,
    _compact_log_block,
)
from game_of_agents.models import AgentRuntime, AgentState, BotSubmission, MatchParticipantResult, MatchResult


def test_dashboard_to_run_state_normalizes_distributed_offer_shape() -> None:
    runtime = object.__new__(ConvexRuntimeClient)
    dashboard = {
        "run": {
            "runId": "run_test123",
            "status": "running",
            "createdAt": 1_773_681_559_317,
            "startedAt": None,
            "finishedAt": None,
            "finalScores": {},
            "payouts": {},
            "config": {
                "name": "test",
                "description": "test",
                "agents": [
                    {
                        "agent_id": "alpha",
                        "runtime": "mock",
                    }
                ],
            },
            "state": {
                "agents": [],
                "bots": [],
                "offers": [
                    {
                        "offer_id": "offer_test123",
                        "seller_agent_id": "alpha",
                        "title": "bundle",
                        "description": "desc",
                        "evidence": "proof",
                        "price_pct": 10.0,
                        "file_paths": ["bot.py", "helpers.py"],
                        "created_at": "2026-03-16T17:00:00Z",
                        "updated_at": "2026-03-16T17:00:00Z",
                    }
                ],
                "purchases": [],
                "reviews": [],
                "games": [],
                "comments": [],
            },
        }
    }

    run = runtime.dashboard_to_run_state(dashboard)

    offer = run.offers["offer_test123"]
    assert offer.artifact_paths == ["bot.py", "helpers.py"]
    assert offer.bot_id == ""


def test_compact_log_block_truncates_oversized_text() -> None:
    huge = "A" * (MAX_LOG_BLOCK_TEXT_CHARS + 5_000)
    compacted = _compact_log_block(
        {
            "block_id": "b1",
            "role": "assistant",
            "kind": "tool_result",
            "text": huge,
        }
    )
    assert len(compacted["text"]) < len(huge)
    assert compacted["text"].startswith("A" * MAX_LOG_BLOCK_TEXT_CHARS)
    assert "truncated 5000 chars" in compacted["text"]


def test_compact_log_block_preserves_short_text() -> None:
    short = "hello world"
    compacted = _compact_log_block(
        {
            "block_id": "b1",
            "role": "assistant",
            "kind": "text",
            "text": short,
        }
    )
    assert compacted["text"] == short


def test_append_log_blocks_chunks_large_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = object.__new__(ConvexRuntimeClient)
    calls: list[tuple[str, dict[str, object]]] = []

    def mutation(name: str, args: dict[str, object]) -> list[dict[str, object]]:
        calls.append((name, args))
        return list(args["blocks"])  # type: ignore[index]

    runtime.mutation = mutation  # type: ignore[method-assign]
    monkeypatch.setattr("game_of_agents.convex_runtime.MAX_CONVEX_MUTATION_BYTES", 1_024)

    blocks = [
        {
            "block_id": f"block_{index}",
            "step_id": "step_1",
            "role": "assistant",
            "kind": "text",
            "title": "Response",
            "text": "x" * 700,
            "collapsed": False,
            "streaming": True,
            "created_at": "2026-03-16T20:00:00Z",
            "updated_at": "2026-03-16T20:00:01Z",
            "ignored": "drop me",
        }
        for index in range(3)
    ]

    written = runtime.append_log_blocks("run_test", agent_id="alpha", blocks=blocks)

    assert len(calls) == 3
    assert all(name == "runtime:appendLogBlocks" for name, _ in calls)
    assert all("ignored" not in payload["blocks"][0] for _, payload in calls)
    assert len(written) == 3


def test_append_match_results_chunks_and_strips_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = object.__new__(ConvexRuntimeClient)
    calls: list[tuple[str, dict[str, object]]] = []

    def mutation(name: str, args: dict[str, object]) -> dict[str, object]:
        calls.append((name, args))
        if name == "runtime:appendMatchSummaries":
            return {"matches": [{"gameId": item["game_id"], "status": item["status"]} for item in args["matches"]]}  # type: ignore[index]
        return {"ok": True}

    runtime.mutation = mutation  # type: ignore[method-assign]
    monkeypatch.setattr("game_of_agents.convex_runtime.MAX_CONVEX_MUTATION_BYTES", 1_400)

    now = datetime.now(tz=UTC)
    matches = [
        MatchResult(
            run_id="run_test",
            status="finished",
            participants=[
                MatchParticipantResult(bot_id="bot_a", agent_id="alpha", seat=0, placement=1, ending_chips=200),
                MatchParticipantResult(bot_id="bot_b", agent_id="beta", seat=1, placement=2, ending_chips=0),
            ],
            winner_bot_id="bot_a",
            loser_bot_id="bot_b",
            reason="showdown",
            started_at=now,
            finished_at=now,
            actions=[{"kind": "verbose", "payload": "x" * 2_000}],
        )
        for _ in range(3)
    ]
    bots = [
        BotSubmission(agent_id="alpha", name="alpha", description="", entrypoint="WorkspaceBot", module_path="bot.py"),
        BotSubmission(agent_id="beta", name="beta", description="", entrypoint="WorkspaceBot", module_path="bot.py"),
    ]
    agents = [
        AgentState(agent_id="alpha", runtime=AgentRuntime.CLAUDE, internet_access=False, workspace="/tmp/alpha"),
        AgentState(agent_id="beta", runtime=AgentRuntime.CLAUDE, internet_access=False, workspace="/tmp/beta"),
    ]

    runtime.append_match_results("run_test", matches=matches, bots=bots, agents=agents)

    summary_calls = [payload for name, payload in calls if name == "runtime:appendMatchSummaries"]
    state_calls = [payload for name, payload in calls if name == "runtime:upsertTournamentState"]
    assert len(summary_calls) >= 2
    assert len(state_calls) >= 1
    # Actions are now preserved in match payloads (needed for analysis)
    assert all("actions" in match for payload in summary_calls for match in payload["matches"])  # type: ignore[index]


def test_summary_to_run_state_tolerates_partial_summary_payload() -> None:
    runtime = object.__new__(ConvexRuntimeClient)

    run = runtime.summary_to_run_state(
        {
            "runId": "run_test123",
            "name": "partial",
            "description": "partial summary",
            "status": "finished",
            "createdAt": 1_773_681_559_317,
            "startedAt": None,
            "finishedAt": 1_773_681_559_999,
        }
    )

    assert run.run_id == "run_test123"
    assert run.config.name == "partial"
    assert run.config.description == "partial summary"
    assert run.config.agents == []
    assert run.final_scores == {}
    assert run.payouts == {}
