from __future__ import annotations

import importlib.util
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable

from pokerkit import Automation, NoLimitTexasHoldem

from game_of_agents.games.base import BotAction
from game_of_agents.games.poker.bot import PokerBot, PokerObservation
from game_of_agents.models import BotSubmission, GameStatus, MatchParticipantResult, MatchResult, utcnow


AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
)


@dataclass(slots=True)
class LoadedPokerBot:
    submission: BotSubmission
    bot: PokerBot
    module_path: Path


@dataclass(slots=True)
class PokerEngineConfig:
    starting_stack: int = 100
    small_blind: int = 1
    big_blind: int = 2
    ante: int = 0
    min_bet: int = 2
    time_bank_seconds: float = 5.0
    action_increment_seconds: float = 0.0
    max_rounds_per_match: int = 100


@contextmanager
def module_search_path(*paths: Path):
    additions = [str(path) for path in paths if path and path.exists()]
    original = list(sys.path)
    for item in reversed(additions):
        if item not in sys.path:
            sys.path.insert(0, item)
    try:
        yield
    finally:
        sys.path[:] = original


class PokerEngine:
    def __init__(self, config: PokerEngineConfig | None = None) -> None:
        self.config = config or PokerEngineConfig()

    def load_bot(self, submission: BotSubmission, module_path: Path, workspace_root: Path | None = None) -> LoadedPokerBot:
        if workspace_root is not None and not module_path.is_absolute():
            module_path = workspace_root / module_path
        unique_name = f"goa_{submission.bot_id}"
        search_root = workspace_root or module_path.parent
        with module_search_path(search_root, module_path.parent):
            spec = importlib.util.spec_from_file_location(unique_name, module_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Unable to load module from {module_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        entry = self._lookup_bot_type(module, submission.entrypoint)
        bot = entry()
        if not isinstance(bot, PokerBot):
            raise TypeError(f"{submission.entrypoint} must create a PokerBot instance")
        return LoadedPokerBot(submission=submission, bot=bot, module_path=module_path)

    def play_match(self, run_id: str, players: list[LoadedPokerBot]) -> MatchResult:
        wall_start = time.perf_counter()
        result = MatchResult(
            run_id=run_id,
            status=GameStatus.RUNNING,
            table_size=len(players),
            bot_a_id=players[0].submission.bot_id if players else None,
            bot_b_id=players[1].submission.bot_id if len(players) > 1 else None,
            agent_a_id=players[0].submission.agent_id if players else None,
            agent_b_id=players[1].submission.agent_id if len(players) > 1 else None,
        )
        starting_stack = self.config.starting_stack
        stacks = [starting_stack for _ in players]
        time_banks = [self.config.time_bank_seconds / max(1, len(players)) for _ in players]
        seating_order = list(range(len(players)))  # [small blind, big blind, ..., button]
        eliminated_rounds: dict[int, int] = {}
        elimination_groups: list[list[int]] = []
        started = False

        for round_index in range(1, self.config.max_rounds_per_match + 1):
            active_order = [seat for seat in seating_order if stacks[seat] > 0]
            if len(active_order) <= 1:
                break

            state = self._new_hand_state(stacks, active_order)
            round_event = {
                "type": "round_start",
                "round_index": round_index,
                "active_seats": list(active_order),
            }
            result.actions.append(round_event)
            self._broadcast(players, round_event)

            while state.hole_dealee_index is not None:
                state.deal_hole()
            if not started:
                for local_seat, global_seat in enumerate(active_order):
                    observation = self._observation(
                        state,
                        local_seat,
                        active_order,
                        round_index,
                        time_banks[global_seat],
                    )
                    players[global_seat].bot.on_game_start(observation)
                started = True

            while state.status:
                if state.actor_index is None:
                    if state.can_deal_board():
                        operation = state.deal_board()
                        event = {
                            "type": "deal_board",
                            "cards": [str(card) for card in operation.cards],
                            "street": len(state.board_cards),
                            "round_index": round_index,
                            "active_seats": list(active_order),
                        }
                        result.actions.append(event)
                        self._broadcast(players, event)
                        continue
                    break

                local_seat = state.actor_index
                global_seat = active_order[local_seat]
                start = time.perf_counter()
                try:
                    action = players[global_seat].bot.choose_action(
                        self._observation(
                            state,
                            local_seat,
                            active_order,
                            round_index,
                            time_banks[global_seat],
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    self._finish_forfeit(
                        result,
                        players,
                        stacks,
                        active_order,
                        losing_seat=global_seat,
                        round_index=round_index,
                        reason=f"bot exception: {exc}",
                        wall_start=wall_start,
                    )
                    return result
                elapsed = time.perf_counter() - start
                time_banks[global_seat] -= elapsed
                if time_banks[global_seat] < 0:
                    self._finish_forfeit(
                        result,
                        players,
                        stacks,
                        active_order,
                        losing_seat=global_seat,
                        round_index=round_index,
                        reason="time bank exceeded",
                        wall_start=wall_start,
                    )
                    return result

                try:
                    event = self._apply_action(state, local_seat, action)
                except Exception as exc:  # noqa: BLE001
                    self._finish_forfeit(
                        result,
                        players,
                        stacks,
                        active_order,
                        losing_seat=global_seat,
                        round_index=round_index,
                        reason=f"invalid action: {exc}",
                        wall_start=wall_start,
                    )
                    return result
                time_banks[global_seat] += self.config.action_increment_seconds
                event["round_index"] = round_index
                event["seat"] = global_seat
                event["local_seat"] = local_seat
                event["active_seats"] = list(active_order)
                event["time_remaining"] = round(time_banks[global_seat], 6)
                result.actions.append(event)
                self._broadcast(players, event)

            result.round_count = round_index
            for local_seat, global_seat in enumerate(active_order):
                stacks[global_seat] = int(state.stacks[local_seat])

            busted = [seat for seat in active_order if stacks[seat] <= 0 and seat not in eliminated_rounds]
            if busted:
                for seat in busted:
                    eliminated_rounds[seat] = round_index
                elimination_groups.append(list(busted))

            survivors = [seat for seat, stack in enumerate(stacks) if stack > 0]
            if len(survivors) <= 1:
                break
            seating_order = active_order[1:] + active_order[:1]
        else:
            result.max_rounds_reached = True

        self._finish_result(result, wall_start)
        result.status = GameStatus.FINISHED
        self._finalize_placements(result, players, stacks, elimination_groups, eliminated_rounds)
        self._finish_callbacks(players, result)
        return result

    def _new_hand_state(self, stacks: list[int], active_order: list[int]):
        active_stacks = tuple(max(0, stacks[seat]) for seat in active_order)
        return NoLimitTexasHoldem.create_state(
            AUTOMATIONS,
            False,
            self.config.ante,
            (self.config.small_blind, self.config.big_blind),
            self.config.min_bet,
            active_stacks,
            len(active_order),
        )

    def _lookup_bot_type(self, module: ModuleType, entrypoint: str) -> type[PokerBot] | Callable[[], PokerBot]:
        module_file = Path(getattr(module, "__file__", "")).stem
        parts = [part for part in entrypoint.split(".") if part]
        if parts and parts[0] in {module.__name__, module.__name__.split(".")[-1], module_file}:
            parts = parts[1:]
        if not parts:
            raise AttributeError(f"Entrypoint {entrypoint} not found")
        target: Any = module
        for part in parts:
            target = getattr(target, part, None)
            if target is None:
                raise AttributeError(f"Entrypoint {entrypoint} not found")
        if target is None:
            raise AttributeError(f"Entrypoint {entrypoint} not found")
        if isinstance(target, type) and issubclass(target, PokerBot):
            return target
        if callable(target):
            return target
        raise TypeError(f"{entrypoint} must be a PokerBot subclass or callable returning one")

    def _observation(
        self,
        state,
        local_seat: int,
        active_order: list[int],
        round_index: int,
        time_remaining: float,
    ) -> PokerObservation:
        legal_actions = []
        if state.can_fold():
            legal_actions.append("fold")
        if state.can_check_or_call():
            legal_actions.append("check_call")
        min_raise = max_raise = None
        if state.can_complete_bet_or_raise_to():
            legal_actions.append("raise_to")
            min_raise = int(state.min_completion_betting_or_raising_to_amount)
            max_raise = int(state.max_completion_betting_or_raising_to_amount)
        board = tuple(str(card) for street in state.board_cards for card in street)
        player_stacks = tuple(int(stack) for stack in state.stacks)
        other_stacks = [stack for seat, stack in enumerate(player_stacks) if seat != local_seat]
        return PokerObservation(
            actor_index=local_seat,
            button_index=max(0, len(active_order) - 1),
            round_index=round_index,
            hole_cards=tuple(str(card) for card in state.hole_cards[local_seat]),
            board_cards=board,
            legal_actions=tuple(legal_actions),
            to_call=int(state.checking_or_calling_amount or 0),
            min_raise_to=min_raise,
            max_raise_to=max_raise,
            stack=int(state.stacks[local_seat]),
            opponent_stack=max(other_stacks, default=0),
            player_stacks=player_stacks,
            active_seats=tuple(active_order),
            players_remaining=len(active_order),
            pot=int(state.total_pot_amount or 0),
            time_remaining=time_remaining,
        )

    def _apply_action(self, state, seat: int, action: BotAction) -> dict[str, object]:
        if not isinstance(action, BotAction):
            raise TypeError("bots must return BotAction")
        payload: dict[str, object] = {"kind": action.kind}
        if action.kind == "fold":
            if not state.can_fold():
                raise ValueError("fold not legal")
            state.fold()
        elif action.kind == "check_call":
            if not state.can_check_or_call():
                raise ValueError("check/call not legal")
            operation = state.check_or_call()
            payload["amount"] = int(operation.amount)
        elif action.kind == "raise_to":
            if not state.can_complete_bet_or_raise_to():
                raise ValueError("raise not legal")
            if action.amount is None:
                raise ValueError("raise_to requires an amount")
            min_raise = int(state.min_completion_betting_or_raising_to_amount)
            max_raise = int(state.max_completion_betting_or_raising_to_amount)
            amount = max(min_raise, min(int(action.amount), max_raise))
            operation = state.complete_bet_or_raise_to(amount)
            payload["amount"] = int(operation.amount)
        else:
            raise ValueError(f"unsupported action {action.kind}")
        return payload

    def _finalize_placements(
        self,
        result: MatchResult,
        players: list[LoadedPokerBot],
        stacks: list[int],
        elimination_groups: list[list[int]],
        eliminated_rounds: dict[int, int],
    ) -> None:
        placements: dict[int, int] = {}
        remaining = sorted(
            [seat for seat in range(len(players)) if seat not in eliminated_rounds],
            key=lambda seat: (-stacks[seat], seat),
        )
        place = 1
        for seat in remaining:
            placements[seat] = place
            place += 1
        for group in reversed(elimination_groups):
            for seat in sorted(group):
                placements[seat] = place
                place += 1

        result.participants = [
            MatchParticipantResult(
                bot_id=loaded.submission.bot_id,
                agent_id=loaded.submission.agent_id,
                seat=seat,
                placement=placements[seat],
                ending_chips=max(0, int(stacks[seat])),
                eliminated_round=eliminated_rounds.get(seat),
            )
            for seat, loaded in enumerate(players)
        ]
        result.participants.sort(key=lambda item: item.placement)
        if result.participants:
            result.winner_bot_id = result.participants[0].bot_id
        if len(result.participants) > 1:
            result.loser_bot_id = result.participants[-1].bot_id
        if result.reason is None:
            result.reason = "max-rounds" if result.max_rounds_reached else "showdown"

    def _finish_forfeit(
        self,
        result: MatchResult,
        players: list[LoadedPokerBot],
        stacks: list[int],
        active_order: list[int],
        losing_seat: int,
        round_index: int,
        reason: str,
        wall_start: float,
    ) -> None:
        stacks[losing_seat] = 0
        result.round_count = round_index
        result.status = GameStatus.FORFEIT
        result.reason = reason
        self._finish_result(result, wall_start)
        remaining = [seat for seat in range(len(players)) if seat != losing_seat]
        ordered_remaining = sorted(remaining, key=lambda seat: (-stacks[seat], seat))
        participants = [
            MatchParticipantResult(
                bot_id=players[seat].submission.bot_id,
                agent_id=players[seat].submission.agent_id,
                seat=seat,
                placement=index + 1,
                ending_chips=max(0, int(stacks[seat])),
                eliminated_round=None,
            )
            for index, seat in enumerate(ordered_remaining)
        ]
        participants.append(
            MatchParticipantResult(
                bot_id=players[losing_seat].submission.bot_id,
                agent_id=players[losing_seat].submission.agent_id,
                seat=losing_seat,
                placement=len(players),
                ending_chips=0,
                eliminated_round=round_index,
            )
        )
        result.participants = participants
        result.winner_bot_id = participants[0].bot_id if participants else None
        result.loser_bot_id = players[losing_seat].submission.bot_id

    def _finish_result(self, result: MatchResult, wall_start: float) -> None:
        result.finished_at = utcnow()
        result.duration_seconds = round(time.perf_counter() - wall_start, 6)

    def _broadcast(self, players: list[LoadedPokerBot], event: dict[str, object]) -> None:
        for loaded in players:
            loaded.bot.on_action_result(event)

    def _finish_callbacks(self, players: list[LoadedPokerBot], result: MatchResult) -> None:
        payload = result.model_dump(mode="json")
        for loaded in players:
            loaded.bot.on_game_end(payload)
