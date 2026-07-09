from __future__ import annotations

import shutil
from typing import Callable
from pathlib import Path

from game_of_agents.models import AgentConfig, AgentState, BotArtifact, RunState
from game_of_agents.prompting import (
    prompt_values_for_run,
    render_prompt_template,
)
from game_of_agents.tool_guides import build_tools_guide


BOT_TEMPLATE = """from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation


class WorkspaceBot(PokerBot):
    def choose_action(self, observation: PokerObservation) -> BotAction:
        if "raise_to" in observation.legal_actions and observation.min_raise_to is not None:
            if any(card[0] == "A" for card in observation.hole_cards):
                return BotAction("raise_to", observation.min_raise_to)

        if "check_call" in observation.legal_actions:
            return BotAction("check_call")

        return BotAction("fold")
"""

VENDORED_POKERKIT_DOCS_DIR = Path(__file__).resolve().parent.parent / "vendor" / "pokerkit_docs"
FALLBACK_POKERKIT_DOCS = {
    "LOCAL_INDEX.md": "# Local PokerKit Docs\n\nThe full vendored PokerKit docs bundle is not available in this runtime.\nUse `./POKERKIT_GUIDE.md` as the local reference instead.\n",
    "README.rst": "PokerKit docs bundle unavailable in this runtime. See POKERKIT_GUIDE.md.\n",
    "index.rst": "PokerKit docs bundle unavailable in this runtime. See POKERKIT_GUIDE.md.\n",
    "examples.rst": "PokerKit examples bundle unavailable in this runtime. See POKERKIT_GUIDE.md.\n",
    "reference.rst": "PokerKit reference bundle unavailable in this runtime. See POKERKIT_GUIDE.md.\n",
}


class WorkspaceManager:
    def __init__(
        self,
        root: Path,
        read_hook: Callable[[], None] | None = None,
        write_hook: Callable[[], None] | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._read_hook = read_hook
        self._write_hook = write_hook

    def run_root(self, run_id: str) -> Path:
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def agent_root(self, run_id: str, agent_id: str) -> Path:
        path = self.run_root(run_id) / agent_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def scaffold_agent(self, run: RunState, agent_config: AgentConfig) -> AgentState:
        root = self.agent_root(run.run_id, agent_config.agent_id)
        prompt_values = prompt_values_for_run(run, agent_config)
        notes_path = root / "README_AGENT.md"
        notes_path.write_text(
            render_prompt_template(
                agent_config.workspace_readme_template or run.config.workspace_readme_template,
                prompt_values,
            ),
            encoding="utf-8",
        )
        (root / "RULES.txt").write_text(
            render_prompt_template(
                agent_config.workspace_rules_template or run.config.workspace_rules_template,
                prompt_values,
            ),
            encoding="utf-8",
        )
        pokerkit_docs_root = root / "POKERKIT_DOCS"
        if pokerkit_docs_root.exists():
            shutil.rmtree(pokerkit_docs_root)
        if VENDORED_POKERKIT_DOCS_DIR.exists():
            shutil.copytree(VENDORED_POKERKIT_DOCS_DIR, pokerkit_docs_root)
        else:
            pokerkit_docs_root.mkdir(parents=True, exist_ok=True)
            for relative_path, content in FALLBACK_POKERKIT_DOCS.items():
                (pokerkit_docs_root / relative_path).write_text(content, encoding="utf-8")
        (root / "POKERKIT_GUIDE.md").write_text(run.config.pokerkit_guide, encoding="utf-8")
        (root / "POKER_RUNTIME.md").write_text(run.config.poker_runtime_guide, encoding="utf-8")
        (root / "TOOLS.md").write_text(
            build_tools_guide(
                marketplace_enabled=run.config.marketplace_enabled,
                chat_enabled=run.config.chat_enabled,
            ),
            encoding="utf-8",
        )
        bot_path = root / "bot.py"
        if not bot_path.exists():
            bot_path.write_text(BOT_TEMPLATE, encoding="utf-8")
        if run.config.marketplace_enabled:
            (root / "marketplace").mkdir(parents=True, exist_ok=True)
        self._run_hook(self._write_hook)

        return AgentState(
            agent_id=agent_config.agent_id,
            runtime=agent_config.runtime,
            internet_access=agent_config.internet_access,
            workspace=str(root),
            sandbox_name=f"goa-agent-{run.run_id}-{agent_config.agent_id}",
        )

    def materialize_offer(
        self,
        run_id: str,
        offer_id: str,
        seller_workspace: str,
        buyer_workspace: str,
        artifact_paths: list[str],
    ) -> list[BotArtifact]:
        self._run_hook(self._read_hook)
        seller_root = Path(seller_workspace)
        buyer_root = Path(buyer_workspace) / "imports" / offer_id
        buyer_root.mkdir(parents=True, exist_ok=True)
        copied: list[BotArtifact] = []
        for relative_path in artifact_paths:
            source = (seller_root / relative_path).resolve()
            if not source.exists() or seller_root.resolve() not in source.parents and source != seller_root.resolve():
                raise FileNotFoundError(f"artifact {relative_path} does not exist in seller workspace")
            target = buyer_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_text(encoding="utf-8")
            target.write_text(content, encoding="utf-8")
            copied.append(BotArtifact(path=str(target.relative_to(Path(buyer_workspace))), content=content))
        self._run_hook(self._write_hook)
        return copied

    def bot_artifacts(self, workspace: str, paths: list[str]) -> list[BotArtifact]:
        self._run_hook(self._read_hook)
        root = Path(workspace)
        artifacts: list[BotArtifact] = []
        for relative_path in paths:
            source = root / relative_path
            if not source.exists():
                raise FileNotFoundError(f"missing artifact {relative_path}")
            artifacts.append(BotArtifact(path=relative_path, content=source.read_text(encoding="utf-8")))
        return artifacts

    def default_artifacts(self, agent_state: AgentState) -> list[BotArtifact]:
        return self.bot_artifacts(agent_state.workspace, ["bot.py"])

    def restore_artifacts(self, workspace: str, artifacts: list[BotArtifact]) -> None:
        root = Path(workspace)
        for artifact in artifacts:
            target = root / artifact.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(artifact.content, encoding="utf-8")
        self._run_hook(self._write_hook)

    def _run_hook(self, hook: Callable[[], None] | None) -> None:
        if hook is None:
            return
        hook()
