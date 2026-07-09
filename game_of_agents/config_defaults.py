from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


PROMPT_DEFAULT_KEYS = (
    "initial_prompt_template",
    "continue_prompt_template",
    "warning_prompt_template",
    "workspace_readme_template",
    "workspace_rules_template",
    "pokerkit_guide",
    "poker_runtime_guide",
)

_FALLBACK_DEFAULTS = {
    "initial_prompt_template": """You are agent {agent_id} in run {run_name}.
Run goal: {run_description}
Agent personality: {agent_goal}
Time left: {minutes_left} minutes.
Marketplace prices are percentages of the buyer's final best-bot tournament score.
Settlement mode: {settlement_mode}. In net mode buyers lose the purchased percentage from their own payout; in additive mode sellers receive that percentage on top.
If you buy an offer, inspect it and leave an honest review as soon as possible.
If you create an offer, include a concrete description and evidence so buyers understand the bundle value.
If you exceed the active bot cap, your current worst-rated active bot is retired automatically.
Use `python -m game_of_agents.agent_tools stats` for your live standing, projected payout, and equity delta.
Use `python -m game_of_agents.agent_tools query-games ...` for filtered structured match data.
Use `python -m game_of_agents.agent_tools show-offer <offer_id>` before buying so you see reviews.""",
    "continue_prompt_template": """Continue working on ./bot.py for agent {agent_id}.
Time left: {minutes_left} minutes.
Best rating: {best_elo}.
Leaderboard rank: #{rank}.
Last step summary: {last_summary}
Marketplace prices are percentages of the buyer's final best-bot tournament score.
Settlement mode: {settlement_mode}. In net mode buyers lose the purchased percentage from their own payout; in additive mode sellers receive that percentage on top.
If you buy an offer, inspect it and leave an honest review before the run ends.
If you create an offer, include a concrete description and evidence tied to your current results or the bundled files.
If you exceed the active bot cap, your current worst-rated active bot is retired automatically.
Use `python -m game_of_agents.agent_tools stats` for your live standing, projected payout, and equity delta.
Use `python -m game_of_agents.agent_tools query-games ...` when you need filtered evidence from recent matches.
Use `python -m game_of_agents.agent_tools show-offer <offer_id>` before buying so you see reviews.""",
    "warning_prompt_template": """Final warning for agent {agent_id} in run {run_name}.
You have {minutes_left} minutes left.
Best rating: {best_elo}.
Leaderboard rank: #{rank}.
Last step summary: {last_summary}
Remember the marketplace settlement math: prices are percentages of the buyer's final best-bot tournament score, and {settlement_mode} decides whether those points come from the buyer or are added on top for the seller.
If you bought something and have not reviewed it yet, do that now.
Use the local stats and query-games tools for the fastest state read instead of guessing from memory.""",
    "workspace_readme_template": """# Agent {agent_id}

Run: {run_name}
Run goal: {run_description}
Agent personality: {agent_goal}
Duration minutes: {duration_minutes}
Settlement mode: {settlement_mode}
Rating spread: {rating_spread}

Marketplace prices are percentages of the buyer's final best-bot tournament score.
In net mode the buyer gives up that percentage from their own final payout.
In additive mode the buyer keeps their own payout and the seller receives extra points on top.
If you buy code, inspect it and leave a review promptly.
Use the local `stats`, `query-games`, and `show-offer` tools instead of scraping the full dashboard.""",
    "workspace_rules_template": """You are competing in a multiplayer NLHE tournament.
Read ./POKER_RUNTIME.md for the required bot API and ./POKERKIT_GUIDE.md for the engine library context.
Your personality: {agent_goal}
Marketplace prices are percentages of the buyer's final best-bot tournament score.
In net mode buyers lose that percentage from their own final payout.
In additive mode sellers receive that percentage on top.
If you buy an offer, inspect the delivered files and leave an honest review promptly.
If you exceed the active bot cap, your current worst-rated active bot is retired automatically.
Use `stats` for live standing and equity, `query-games` for filtered match evidence, and `show-offer` before buying.""",
    "pokerkit_guide": """# PokerKit Guide

This project uses PokerKit as the underlying poker engine library.

Read the local docs in ./POKERKIT_DOCS, starting with ./POKERKIT_DOCS/LOCAL_INDEX.md.
Official docs:
- https://pokerkit.readthedocs.io/en/""",
    "poker_runtime_guide": """# Poker Runtime Guide

Entrypoint:
- Keep the class name `WorkspaceBot`.
- `WorkspaceBot` must subclass `PokerBot`.
- Required method: `choose_action(self, observation: PokerObservation) -> BotAction`.""",
}


def _default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "dashboard_default_run.yaml"


@lru_cache(maxsize=1)
def load_default_run_config() -> dict[str, object]:
    path = _default_config_path()
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def default_prompt_value(key: str) -> str:
    raw = load_default_run_config().get(key)
    if isinstance(raw, str) and raw.strip():
        return raw
    return _FALLBACK_DEFAULTS[key]
