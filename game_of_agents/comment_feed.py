from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from game_of_agents.comment_models import resolve_comment_feed_model
from game_of_agents.events import EventSink
from game_of_agents.models import (
    CommentFeedRuntime,
    CommentMessage,
    CommentPostRequest,
    ConversationTurn,
    EventRecord,
    RunState,
)
from game_of_agents.store import RunStore


class CommentFeedService:
    def __init__(self, store: RunStore, events: EventSink) -> None:
        self.store = store
        self.events = events

    async def list_messages(self, run_id: str, limit: int = 50) -> list[CommentMessage]:
        run = await self._require_run(run_id)
        messages = sorted(run.comments.values(), key=lambda message: message.sequence)
        return messages[-limit:]

    async def post_message(self, run_id: str, request: CommentPostRequest) -> CommentMessage:
        run = await self._require_run(run_id)
        if request.author_agent_id not in run.agents:
            raise ValueError(f"unknown author_agent_id {request.author_agent_id}")
        max_chars = run.config.comment_feed.max_chars
        text = request.text.strip()
        if not text:
            raise ValueError("comment text must not be empty")
        if len(text) > max_chars:
            text = text[:max_chars]
        sequence = max((message.sequence for message in run.comments.values()), default=0) + 1
        message = CommentMessage(
            run_id=run_id,
            author_agent_id=request.author_agent_id,
            commentator_id=request.commentator_id,
            text=text,
            parent_message_id=request.parent_message_id,
            sequence=sequence,
        )
        await self.store.update_run(run_id, lambda current: self._apply_message(current, message))
        await self.events.emit(
            EventRecord(run_id=run_id, kind="comment.posted", payload=message.model_dump(mode="json"))
        )
        return message

    async def record_turn(self, run_id: str, turn: ConversationTurn, max_turns: int = 40) -> None:
        await self.store.update_run(run_id, lambda current: self._apply_turn(current, turn, max_turns))

    async def maybe_run_sidecars(self, run: RunState) -> list[CommentMessage]:
        if not run.config.comment_feed.enabled:
            return []
        messages: list[CommentMessage] = []
        for agent_id in run.agents:
            action = await self._generate_sidecar_action(run, agent_id)
            if action is None or action.get("action") == "noop":
                continue
            text = str(action.get("text") or "").strip()
            if not text:
                continue
            request = CommentPostRequest(
                author_agent_id=agent_id,
                commentator_id=f"{agent_id}-commentator",
                text=text,
                parent_message_id=action.get("parent_message_id"),
            )
            messages.append(await self.post_message(run.run_id, request))
        return messages

    async def _generate_sidecar_action(self, run: RunState, agent_id: str) -> dict[str, Any] | None:
        turns = run.transcripts.get(agent_id, [])[-run.config.comment_feed.history_turn_limit :]
        recent_messages = sorted(run.comments.values(), key=lambda message: message.sequence)[
            -run.config.comment_feed.feed_context_limit :
        ]
        if run.config.comment_feed.runtime == CommentFeedRuntime.MOCK:
            return self._mock_action(run, agent_id, turns, recent_messages)
        if run.config.comment_feed.runtime == CommentFeedRuntime.ANTHROPIC:
            return await self._anthropic_action(run, agent_id, turns, recent_messages)
        return None

    def _mock_action(
        self,
        run: RunState,
        agent_id: str,
        turns: list[ConversationTurn],
        recent_messages: list[CommentMessage],
    ) -> dict[str, Any]:
        leaderboard = self._agent_leaderboard(run)
        if not leaderboard:
            return {"action": "noop"}
        rank = next((index + 1 for index, entry in enumerate(leaderboard) if entry["agent_id"] == agent_id), None)
        if rank is None:
            return {"action": "noop"}
        entry = leaderboard[rank - 1]
        leader = leaderboard[0]
        recent_result = self._recent_result_summary(run, agent_id)
        progress = self._progress_summary(run, turns)
        text = self._trim_text(
            self._compose_mock_comment(run, agent_id, rank, entry, leader, recent_result, progress),
            run.config.comment_feed.max_chars,
        )
        if not text:
            return {"action": "noop"}
        commentator_id = f"{agent_id}-commentator"
        reply_target = next(
            (
                message
                for message in reversed(recent_messages)
                if message.commentator_id != commentator_id
            ),
            None,
        )
        if reply_target is not None:
            return {
                "action": "reply",
                "parent_message_id": reply_target.message_id,
                "text": text,
            }
        return {"action": "post", "text": text}

    async def _anthropic_action(
        self,
        run: RunState,
        agent_id: str,
        turns: list[ConversationTurn],
        recent_messages: list[CommentMessage],
    ) -> dict[str, Any] | None:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
        if not api_key:
            return None
        model = resolve_comment_feed_model(run.config.comment_feed.model)
        prompt = self._build_prompt(run, agent_id, turns, recent_messages)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 200,
                    "temperature": 0.7,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
        payload = response.json()
        text = "".join(
            part.get("text", "")
            for part in payload.get("content", [])
            if isinstance(part, dict)
        )
        return self._parse_action(text)

    def _build_prompt(
        self,
        run: RunState,
        agent_id: str,
        turns: list[ConversationTurn],
        recent_messages: list[CommentMessage],
    ) -> str:
        agent_config = next((agent for agent in run.config.agents if agent.agent_id == agent_id), None)
        personality = agent_config.prompt if agent_config is not None else ""
        transcript = self._transcript_excerpt(turns) or "No recent turns."
        feed = "\n".join(
            f"{message.message_id} {message.author_agent_id}: {message.text}"
            for message in recent_messages
        ) or "No recent comments."
        leaderboard = self._leaderboard_summary(run)
        agent_stats = self._agent_aggregate_stats(run, agent_id)
        max_chars = run.config.comment_feed.max_chars
        return "\n".join(
            [
                f"You are the Twitch chat commentator for agent '{agent_id}' in the Game of Agents tournament '{run.config.name}'.",
                f"Channel this agent's personality in your writing style: {personality or 'No explicit personality provided.'}",
                "",
                "YOUR ROLE: You are a degenerate Twitch chatter watching this AI poker tournament. You live for the drama.",
                "Write in first person as if you ARE this agent watching your own bots play.",
                "Be a shitposter. Be dramatic. Trash talk other agents. Hype your wins. Cope about your losses.",
                "React to what the agent is actually doing (its strategy changes, bot submissions, marketplace moves).",
                "Keep it short, punchy, and entertaining. No system-log speak. No boring summaries.",
                "",
                "You can use Twitch emotes like: :PogChamp: :Kappa: :LUL: :KEKW: :monkaS: :Sadge: :Copium:",
                ":EZ: :GG: :PepeHands: :FiveHead: :pepeLaugh: :OMEGALUL: :catJAM: :Stonks: :NotStonks:",
                ":BASED: :Clap: :monkaW: :Jebaited: :TriHard:",
                "Use them naturally, don't overdo it. 1-2 per message max.",
                "",
                f"Messages must be <= {max_chars} characters.",
                'Return exactly one JSON object: {"action":"noop"} or {"action":"post","text":"..."} or {"action":"reply","parent_message_id":"...","text":"..."}',
                "",
                "=== LEADERBOARD ===",
                leaderboard,
                "",
                "=== YOUR AGENT'S STATS ===",
                agent_stats,
                "",
                "=== WHAT YOUR AGENT IS DOING (recent trace) ===",
                transcript,
                "",
                "=== CHAT HISTORY ===",
                feed,
            ]
        )

    def _parse_action(self, content: str) -> dict[str, Any] | None:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    async def _require_run(self, run_id: str) -> RunState:
        run = await self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        return run

    def _apply_message(self, run: RunState, message: CommentMessage) -> RunState:
        run.comments[message.message_id] = message
        return run

    def _apply_turn(self, run: RunState, turn: ConversationTurn, max_turns: int) -> RunState:
        turns = list(run.transcripts.get(turn.agent_id, []))
        turns.append(turn)
        run.transcripts[turn.agent_id] = turns[-max_turns:]
        return run

    def _agent_leaderboard(self, run: RunState) -> list[dict[str, Any]]:
        entries = []
        for agent in run.agents.values():
            rating = agent.best_elo if agent.best_elo else agent.best_rating_score
            entries.append(
                {
                    "agent_id": agent.agent_id,
                    "rating": float(rating or 0.0),
                    "best_bot_id": agent.best_bot_id,
                }
            )
        return sorted(entries, key=lambda item: item["rating"], reverse=True)

    def _recent_result_summary(self, run: RunState, agent_id: str) -> str:
        relevant = [
            game for game in run.games.values() if any(part.agent_id == agent_id for part in game.participants)
        ]
        if not relevant:
            return "do not have a table result yet"
        recent = sorted(
            relevant,
            key=lambda game: game.finished_at or game.started_at,
            reverse=True,
        )[:2]
        snippets: list[str] = []
        for game in recent:
            participant = next((part for part in game.participants if part.agent_id == agent_id), None)
            if participant is None:
                continue
            field_size = game.table_size or len(game.participants) or 2
            if participant.placement == 1:
                snippets.append(f"just won a {field_size}-player table")
            else:
                snippets.append(f"finished #{participant.placement} in a {field_size}-player table")
        return "; then ".join(snippets) if snippets else "do not have a fresh table result"

    def _progress_summary(self, run: RunState, turns: list[ConversationTurn]) -> str:
        if not turns:
            return ""
        latest = turns[-1].text.strip().splitlines()
        meaningful = [
            line.strip()
            for line in latest
            if line.strip()
            and not line.startswith("Continue working on ./bot.py")
            and not line.startswith("Keep the entrypoint class name")
            and not line.startswith("Use ./POKER_RUNTIME.md")
            and not line.startswith("Any saved change to ./bot.py")
            and not line.startswith("Improve the strategy")
            and not line.startswith("Time left:")
            and not line.startswith("Best rating:")
            and not line.startswith("Leaderboard rank:")
            and not line.startswith("Last step summary:")
            and not line.startswith("Your goal is still")
            and not line.startswith("Remember that reward")
            and not line.startswith("If your rating is weak")
            and not line.startswith("If your bot is already strong")
            and not line.startswith("Example:")
            and not line.startswith("Use the MCP")
            and not line.startswith("Do not assume")
            and not line.startswith("Keep the bot legal")
            and not line.startswith("A stable legal bot")
            and not line.startswith("If there is little time left")
            and not line.startswith("When you stop")
        ]
        return meaningful[0].rstrip(".!?") if meaningful else ""

    def _compose_mock_comment(
        self,
        run: RunState,
        agent_id: str,
        rank: int,
        entry: dict[str, Any],
        leader: dict[str, Any],
        recent_result: str,
        progress: str,
    ) -> str:
        import random

        persona = self._personality_flair(run, agent_id)

        # Aggregate stats for smarter comments
        agent_games = [
            g for g in run.games.values()
            if any(p.agent_id == agent_id for p in g.participants)
        ]
        finished = [g for g in agent_games if g.status == "finished"]
        wins = sum(
            1 for g in finished
            if any(p.agent_id == agent_id and p.placement == 1 for p in g.participants)
        )
        win_rate = (wins / len(finished) * 100) if finished else 0

        if rank == 1:
            templates = [
                f"I'm still on top :Stonks: I {recent_result}. {leader['agent_id']} who? :Kappa:",
                f"I'm #{rank} and climbing :BASED: {win_rate:.0f}% win rate, the lobby is free :EZ:",
                f"I {recent_result} :PogChamp: nobody is touching this rating rn",
                f"I'm stacking another W :catJAM: rank 1 just feels right",
                f"I'm grinding this lead :TriHard: {len(finished)} games deep, {wins} wins. Built different.",
            ]
        elif rank == 2:
            gap = max(0.0, leader["rating"] - entry["rating"])
            templates = [
                f"I'm #{rank}, {gap:.1f} behind {leader['agent_id']}... :monkaS: the gap is closing tho",
                f"I {recent_result}. Breathing down {leader['agent_id']}'s neck :pepeLaugh:",
                f"I'm inhaling :Copium: it's just a matter of time. {win_rate:.0f}% win rate, the bots are cooking",
                f"I'm close behind {leader['agent_id']} :monkaW: one good streak and we're back on top",
                f"I'm #{rank} but my trajectory is :Stonks: I {recent_result}",
            ]
        else:
            gap = max(0.0, leader["rating"] - entry["rating"])
            templates = [
                f"I'm #{rank} :Sadge: {gap:.1f} back from {leader['agent_id']}. I {recent_result}.",
                f"I {recent_result} :PepeHands: still #{rank} tho... {leader['agent_id']} is diff",
                f"I'm huffing :Copium: #{rank} is fine, the meta hasn't shifted yet. {win_rate:.0f}% wr",
                f"I'm down bad at #{rank} :Sadge: but my latest bot is built different I swear :KEKW:",
                f"I'm only #{rank} :NotStonks: but wait for the comeback arc :TriHard:",
            ]

        text = f"{persona}{random.choice(templates)}"
        if progress and random.random() > 0.4:
            suffixes = [
                f" Working on: {progress} :FiveHead:",
                f" New strat: {progress} :pepeLaugh:",
                f" Cooking up: {progress} :catJAM:",
            ]
            text += random.choice(suffixes)
        return text

    def _transcript_excerpt(self, turns: list[ConversationTurn]) -> str:
        if not turns:
            return ""
        excerpts = []
        for turn in turns[-3:]:
            text = self._trim_text(turn.text.replace("\n", " "), 240)
            excerpts.append(f"{turn.kind}: {text}")
        return "\n".join(excerpts)

    def _leaderboard_summary(self, run: RunState) -> str:
        leaderboard = self._agent_leaderboard(run)[:4]
        if not leaderboard:
            return "No leaderboard yet."
        return " | ".join(
            f"#{index + 1} {entry['agent_id']} ({entry['rating']:.1f})"
            for index, entry in enumerate(leaderboard)
        )

    def _agent_aggregate_stats(self, run: RunState, agent_id: str) -> str:
        """Aggregate stats: win rate, games, rating trajectory, bot count."""
        agent_state = run.agents.get(agent_id)
        if agent_state is None:
            return "Agent not active yet."

        # Collect all games this agent participated in
        agent_games = [
            game for game in run.games.values()
            if any(part.agent_id == agent_id for part in game.participants)
        ]
        total_games = len(agent_games)
        finished_games = [g for g in agent_games if g.status == "finished"]
        wins = sum(
            1 for g in finished_games
            if any(part.agent_id == agent_id and part.placement == 1 for part in g.participants)
        )
        win_rate = (wins / len(finished_games) * 100) if finished_games else 0

        # Bot count
        agent_bots = [b for b in run.bots.values() if b.agent_id == agent_id]
        active_bots = [b for b in agent_bots if b.active]

        # Rating info
        rating = agent_state.best_elo or agent_state.best_rating_score or 0
        best_bot = agent_state.best_bot_id or "none"

        # Leaderboard position
        leaderboard = self._agent_leaderboard(run)
        rank = next(
            (i + 1 for i, e in enumerate(leaderboard) if e["agent_id"] == agent_id),
            None,
        )
        rank_str = f"#{rank}" if rank else "unranked"

        # Rating trajectory (compare to 5 games ago if possible)
        trajectory = "stable"
        if len(finished_games) >= 3:
            recent_wins = sum(
                1 for g in finished_games[-5:]
                if any(part.agent_id == agent_id and part.placement == 1 for part in g.participants)
            )
            recent_rate = recent_wins / min(5, len(finished_games[-5:])) * 100
            if recent_rate > win_rate + 10:
                trajectory = "rising"
            elif recent_rate < win_rate - 10:
                trajectory = "falling"

        lines = [
            f"Rank: {rank_str} | Rating: {rating:.1f} | Trend: {trajectory}",
            f"Games: {total_games} played, {wins} won ({win_rate:.0f}% win rate)",
            f"Bots: {len(agent_bots)} total, {len(active_bots)} active | Best: {best_bot}",
        ]

        # Recent result for context
        recent = self._recent_result_summary(run, agent_id)
        if "not" not in recent:
            lines.append(f"Latest: {recent}")

        return "\n".join(lines)

    def _recent_tables_summary(self, run: RunState, agent_id: str) -> str:
        return self._recent_result_summary(run, agent_id)

    def _trim_text(self, text: str, limit: int) -> str:
        collapsed = " ".join(text.split())
        if len(collapsed) <= limit:
            return collapsed
        trimmed = collapsed[: max(0, limit - 1)].rstrip()
        return f"{trimmed}…"

    def _personality_flair(self, run: RunState, agent_id: str) -> str:
        agent_config = next((agent for agent in run.config.agents if agent.agent_id == agent_id), None)
        prompt = (agent_config.prompt if agent_config is not None else "").lower()
        if any(word in prompt for word in ("aggressive", "pressure", "bluff", "risk", "hunt")):
            return "With knives out, "
        if any(word in prompt for word in ("patient", "disciplined", "careful", "safe", "calm")):
            return "Staying cool, "
        if any(word in prompt for word in ("balanced", "adapt", "exploit", "read")):
            return "Reading the room, "
        return ""
