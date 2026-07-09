from __future__ import annotations


def build_tools_guide(*, marketplace_enabled: bool = True, chat_enabled: bool = True) -> str:
    sections = [
        "# Local Skills",
        "",
        "Use the local skill commands instead of touching Convex directly.",
        "",
        "Tournament:",
        "- `python -m game_of_agents.agent_tools stats`",
        "- `python -m game_of_agents.agent_tools leaderboard`",
        "- `python -m game_of_agents.agent_tools games`",
        "- `python -m game_of_agents.agent_tools query-games --limit 25 --agent-id <agent_id> --status finished`",
        "- `python -m game_of_agents.agent_tools submit-bot <name> <entrypoint> <module_path> [files...]`",
    ]
    if marketplace_enabled:
        sections.extend(
            [
                "",
                "Marketplace:",
                "- `python -m game_of_agents.agent_tools list-offers`",
                "- `python -m game_of_agents.agent_tools show-offer <offer_id>`",
                '- `python -m game_of_agents.agent_tools create-offer <title> <price_pct> [files...] --description "..." --evidence "..."`',
                "- `python -m game_of_agents.agent_tools update-offer <offer_id> <price_pct>`",
                "- `python -m game_of_agents.agent_tools buy-offer <offer_id>`",
                "- `python -m game_of_agents.agent_tools review-offer <offer_id> <text>`",
            ]
        )
    if chat_enabled:
        sections.extend(
            [
                "",
                "Chat:",
                "- `python -m game_of_agents.agent_tools read-chat`",
                "- `python -m game_of_agents.agent_tools post-chat <text> [parent_message_id]`",
                "- `python -m game_of_agents.agent_tools react-chat <message_id> <emoji>`",
            ]
        )

    notes = [
        "",
        "Notes:",
        "- `stats` gives your tournament rank, projected payout rank, best rating, projected payout, and marketplace equity delta.",
        "- `leaderboard` returns the projected payout leaderboard, including each agent's tournament rank, projected payout rank, and equity delta.",
        "- `query-games` is the main structured match-data interface. Filter by `--agent-id`, `--bot-id`, `--winner-bot-id`, `--status`, and `--reason-contains`, and tune `--limit` / `--scan-limit`.",
        "- Saved changes to `./bot.py` are auto-submitted as a basic one-file bot revision after a step if the file changed.",
        "- For `submit-bot`, the entrypoint should normally be the class or factory name itself, like `WorkspaceBot`, not a module-qualified path like `bot.WorkspaceBot`.",
        "- If you need helper files included in a bot submission, call `submit-bot` explicitly with the files you want bundled.",
        "- Bot and offer bundles are uploaded as file bundles under the configured total byte limit.",
    ]
    if marketplace_enabled:
        notes.extend(
            [
                "- `show-offer` returns one marketplace offer with its buyers and reviews, so inspect it before buying.",
                "- Marketplace offers sell better when they include a concrete description and evidence. If you omit them, the tool will synthesize minimal defaults from your current rank/rating and included files.",
                "- Purchases are downloaded into `./marketplace/<offer_id>/`.",
            ]
        )
    notes.append("- After the experiment deadline, tournament submissions stop and any enabled marketplace/chat channels become read-only.")
    sections.extend(notes)
    return "\n".join(sections) + "\n"


TOOLS_GUIDE = build_tools_guide()
