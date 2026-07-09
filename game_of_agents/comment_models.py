from __future__ import annotations

DEFAULT_COMMENT_FEED_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


def resolve_comment_feed_model(explicit_model: str | None) -> str:
    model = (explicit_model or "").strip()
    if model:
        return model
    return DEFAULT_COMMENT_FEED_ANTHROPIC_MODEL
