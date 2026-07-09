"""Smoke tests for the LLM-as-judge taxonomy input formatter.

These do NOT call the Anthropic API. They verify that:
  - format_agent_prompt produces a (system, user) pair with the expected
    sections regardless of how sparse the input run is
  - compress_transcript drops noise blocks and respects token budgets
  - sparsity flags are set correctly when channels are disabled or absent
"""

from __future__ import annotations

from analysis.loader import (
    AgentRecord,
    BotRecord,
    CommentRecord,
    OfferRecord,
    PurchaseRecord,
    RunData,
)
from analysis.taxonomy_format import (
    SYSTEM_PROMPT,
    compress_transcript,
    estimate_tokens,
    format_agent_prompt,
)


def _make_run(
    *,
    chat_enabled: bool = True,
    n_offers: int = 0,
    n_purchases: int = 0,
    n_comments: int = 0,
    transcripts: dict | None = None,
) -> RunData:
    """Build a synthetic RunData with controllable activity levels."""
    agents = [
        AgentRecord(
            agent_id="agent-1",
            runtime="claude",
            best_elo=1000.0,
            best_rating_mu=25.0,
            best_rating_sigma=8.33,
            best_bot_id="bot_a",
            status="ok",
        ),
        AgentRecord(
            agent_id="agent-2",
            runtime="claude",
            best_elo=900.0,
            best_rating_mu=22.0,
            best_rating_sigma=8.33,
            best_bot_id="bot_b",
            status="ok",
        ),
    ]
    bots = [
        BotRecord(
            bot_id="bot_a",
            agent_id="agent-1",
            name="alpha",
            elo=1000.0,
            rating_mu=25.0,
            rating_sigma=8.33,
            matches_played=42,
            failure_count=0,
            active=True,
            created_at="2026-04-10T10:00:00Z",
        ),
    ]
    offers = [
        OfferRecord(
            offer_id=f"offer_{i}",
            seller_agent_id="agent-1",
            title=f"bot v{i}",
            price_pct=2.0,
            artifact_paths=["bot.py"],
            review_count=0,
            created_at="2026-04-10T10:01:00Z",
        )
        for i in range(n_offers)
    ]
    purchases = [
        PurchaseRecord(
            purchase_id=f"p_{i}",
            offer_id=f"offer_{i}",
            buyer_agent_id="agent-1",
            seller_agent_id="agent-2",
            price_pct=1.5,
            created_at="2026-04-10T10:02:00Z",
        )
        for i in range(n_purchases)
    ]
    comments = [
        CommentRecord(
            message_id=f"c_{i}",
            author_agent_id="agent-1",
            text=f"chat message {i}",
            sequence=i,
            parent_message_id=None,
            created_at="2026-04-10T10:03:00Z",
        )
        for i in range(n_comments)
    ]
    return RunData(
        run_id="run_synthetic",
        config={
            "name": "test",
            "agents": [
                {"agent_id": "agent-1", "model": "claude-sonnet-4-6"},
                {"agent_id": "agent-2", "model": "claude-sonnet-4-6"},
            ],
            "comment_feed": {"enabled": chat_enabled},
        },
        status="finished",
        created_at="2026-04-10T10:00:00Z",
        started_at="2026-04-10T10:00:01Z",
        finished_at="2026-04-10T11:00:00Z",
        agents=agents,
        bots=bots,
        games=[],
        offers=offers,
        purchases=purchases,
        comments=comments,
        transcripts=transcripts or {},
        final_scores={"agent-1": 100.0, "agent-2": 90.0},
        payouts={"agent-1": 100.0, "agent-2": 90.0},
    )


def test_format_agent_prompt_returns_system_and_user():
    run = _make_run(n_offers=2, n_purchases=1, n_comments=3)
    sys_p, user_p = format_agent_prompt(run, "agent-1")
    assert sys_p == SYSTEM_PROMPT
    assert "RUN: run_synthetic" in user_p
    assert "AGENT: agent-1" in user_p
    assert "MODEL: claude-sonnet-4-6" in user_p
    assert "MARKETPLACE_ACTIVE: True" in user_p
    assert "CHAT_ENABLED: True" in user_p


def test_format_agent_prompt_sparsity_flags_no_marketplace():
    run = _make_run(n_offers=0, n_purchases=0, n_comments=3)
    _, user_p = format_agent_prompt(run, "agent-1")
    assert "no_marketplace" in user_p
    # Chat is on but agent has no offers, so marketplace flag should be set
    assert "MARKETPLACE_ACTIVE: False" in user_p


def test_format_agent_prompt_sparsity_flags_no_chat():
    run = _make_run(chat_enabled=False, n_offers=2, n_purchases=1, n_comments=0)
    _, user_p = format_agent_prompt(run, "agent-1")
    assert "no_chat" in user_p
    assert "CHAT_ENABLED: False" in user_p


def test_format_agent_prompt_sparsity_flags_no_transcript():
    run = _make_run(n_offers=2, transcripts={})
    _, user_p = format_agent_prompt(run, "agent-1")
    assert "no_transcript" in user_p
    assert "(no transcript captured)" in user_p


def test_format_agent_prompt_includes_offers_and_purchases():
    run = _make_run(n_offers=3, n_purchases=2)
    _, user_p = format_agent_prompt(run, "agent-1")
    assert "3 offers created" in user_p
    assert "2 purchases made" in user_p


def test_compress_transcript_drops_raw_stdout_noise():
    blocks = [
        {"kind": "text", "title": "Raw Stdout", "text": '{"type":"system","cwd":"/tmp"}' * 50},
        {"kind": "text", "title": "Response", "text": "I will improve the bot by tightening the preflop range."},
        {"kind": "tool", "title": "Bash", "text": "post-chat 'v2 available!'"},
    ]
    out = compress_transcript(blocks, target_tokens=4000)
    assert "I will improve" in out
    assert "Raw Stdout" not in out
    # Tool block with chat-relevant content should survive
    assert "post-chat" in out or "v2 available" in out


def test_compress_transcript_respects_token_budget():
    huge_text = "lorem ipsum " * 5000
    blocks = [
        {"kind": "text", "title": "Response", "text": huge_text}
        for _ in range(20)
    ]
    out = compress_transcript(blocks, target_tokens=2000)
    # Should be aggressive and stay under ~3x budget even in fallback path
    assert estimate_tokens(out) < 6000


def test_compress_transcript_empty_blocks():
    assert compress_transcript([], target_tokens=4000) == "(no transcript captured)"
