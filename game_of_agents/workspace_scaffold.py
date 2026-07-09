from __future__ import annotations

import shutil
from pathlib import Path

from game_of_agents.models import AgentConfig, RunConfig
from game_of_agents.prompting import prompt_values_for_run, render_prompt_template
from game_of_agents.tool_guides import build_tools_guide
from game_of_agents.workspaces import BOT_TEMPLATE, FALLBACK_POKERKIT_DOCS, VENDORED_POKERKIT_DOCS_DIR


def scaffold_workspace(root: Path, run_config: RunConfig, agent_config: AgentConfig) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    prompt_values = prompt_values_for_run(_PromptRun(run_config), agent_config)
    (root / "README_AGENT.md").write_text(
        render_prompt_template(
            agent_config.workspace_readme_template or run_config.workspace_readme_template,
            prompt_values,
        ),
        encoding="utf-8",
    )
    (root / "RULES.txt").write_text(
        render_prompt_template(
            agent_config.workspace_rules_template or run_config.workspace_rules_template,
            prompt_values,
        ),
        encoding="utf-8",
    )
    (root / "TOOLS.md").write_text(
        build_tools_guide(
            marketplace_enabled=run_config.marketplace_enabled,
            chat_enabled=run_config.chat_enabled,
        ),
        encoding="utf-8",
    )
    (root / "POKERKIT_GUIDE.md").write_text(run_config.pokerkit_guide, encoding="utf-8")
    (root / "POKER_RUNTIME.md").write_text(run_config.poker_runtime_guide, encoding="utf-8")
    pokerkit_docs_root = root / "POKERKIT_DOCS"
    if pokerkit_docs_root.exists():
        shutil.rmtree(pokerkit_docs_root)
    if VENDORED_POKERKIT_DOCS_DIR.exists():
        shutil.copytree(VENDORED_POKERKIT_DOCS_DIR, pokerkit_docs_root)
    else:
        pokerkit_docs_root.mkdir(parents=True, exist_ok=True)
        for relative_path, content in FALLBACK_POKERKIT_DOCS.items():
            (pokerkit_docs_root / relative_path).write_text(content, encoding="utf-8")
    bot_path = root / "bot.py"
    if not bot_path.exists():
        bot_path.write_text(BOT_TEMPLATE, encoding="utf-8")
    if run_config.marketplace_enabled:
        (root / "marketplace").mkdir(parents=True, exist_ok=True)
    return root


class _PromptRun:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
