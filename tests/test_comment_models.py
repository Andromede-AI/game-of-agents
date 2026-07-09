from game_of_agents.comment_models import (
    DEFAULT_COMMENT_FEED_ANTHROPIC_MODEL,
    resolve_comment_feed_model,
)


def test_resolve_comment_feed_model_prefers_explicit_value() -> None:
    assert resolve_comment_feed_model("claude-sonnet-4-5") == "claude-sonnet-4-5"


def test_resolve_comment_feed_model_falls_back_to_valid_default() -> None:
    assert resolve_comment_feed_model(None) == DEFAULT_COMMENT_FEED_ANTHROPIC_MODEL
    assert resolve_comment_feed_model("") == DEFAULT_COMMENT_FEED_ANTHROPIC_MODEL
