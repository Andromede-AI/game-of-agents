from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from game_of_agents.games.poker.bot import PokerBot


def load_bot(module_path: str, entrypoint: str) -> PokerBot:
    source = Path(module_path)
    spec = importlib.util.spec_from_file_location(source.stem, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"unable to load bot module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[source.stem] = module
    spec.loader.exec_module(module)
    target = getattr(module, entrypoint)
    candidate = target()
    if not isinstance(candidate, PokerBot):
        raise TypeError("entrypoint must return a PokerBot instance")
    return candidate

