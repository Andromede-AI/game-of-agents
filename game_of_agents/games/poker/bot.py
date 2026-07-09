from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from game_of_agents.games.base import BotAction


@dataclass(slots=True)
class PokerObservation:
    actor_index: int
    button_index: int
    round_index: int
    hole_cards: tuple[str, ...]
    board_cards: tuple[str, ...]
    legal_actions: tuple[str, ...]
    to_call: int
    min_raise_to: int | None
    max_raise_to: int | None
    stack: int
    opponent_stack: int
    player_stacks: tuple[int, ...]
    active_seats: tuple[int, ...]
    players_remaining: int
    pot: int
    time_remaining: float


class PokerBot(ABC):
    """Minimal runtime contract for submitted poker bots.

    Legacy heads-up bots remain valid: `opponent_stack` is still populated with the
    largest non-actor stack, while multiplayer-aware bots can read `player_stacks`,
    `active_seats`, and `players_remaining`.
    """

    def on_game_start(self, observation: PokerObservation) -> None:
        """Optional hook for initialization."""

    def on_action_result(self, action_event: dict[str, object]) -> None:
        """Optional hook to observe every resolved action."""

    def on_game_end(self, result: dict[str, object]) -> None:
        """Optional hook for cleanup."""

    @abstractmethod
    def choose_action(self, observation: PokerObservation) -> BotAction:
        """Return the next action for the acting seat."""


PokerAction = BotAction
