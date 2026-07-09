from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ActionKind = Literal["fold", "check_call", "raise_to"]


@dataclass(slots=True)
class BotAction:
    kind: ActionKind
    amount: int | None = None

