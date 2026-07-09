from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
from datetime import UTC, datetime
from typing import Any, Iterable

from convex import ConvexClient

from game_of_agents.json import canonical_dumps, dumps
from game_of_agents.logging import get_logger
from game_of_agents.models import EventRecord, RunState
from game_of_agents.settings import settings

logger = get_logger(__name__)

SYNC_KINDS = (
    "agents",
    "bots",
    "games",
    "offers",
    "purchases",
    "reviews",
    "comments",
)
MAX_MUTATION_BYTES = 256_000
SNAPSHOT_TOP_AGENTS = 32
SNAPSHOT_TOP_BOTS = 64


@dataclass
class _RunSyncState:
    item_hashes: dict[str, dict[str, str]] = field(
        default_factory=lambda: {kind: {} for kind in SYNC_KINDS}
    )


class ConvexSync:
    def __init__(self, url: str, sync_token: str | None = None) -> None:
        self.client = ConvexClient(url)
        self.sync_token = sync_token
        self._lock = asyncio.Lock()
        self._state: dict[str, _RunSyncState] = {}

    async def sync_run(self, run: RunState) -> None:
        summary = self._build_summary(run)
        await self._call(
            "runs:syncRunSummary",
            {"summary": summary, "syncToken": self.sync_token},
        )
        state = self._state.setdefault(run.run_id, _RunSyncState())
        collections = {
            "agents": [agent.model_dump(mode="json") for agent in run.agents.values()],
            "bots": [bot.model_dump(mode="json") for bot in run.bots.values()],
            "games": [self._compact_game_payload(game) for game in run.games.values()],
            "offers": [offer.model_dump(mode="json") for offer in run.offers.values()],
            "purchases": [purchase.model_dump(mode="json") for purchase in run.purchases.values()],
            "reviews": [review.model_dump(mode="json") for review in run.reviews.values()],
            "comments": [comment.model_dump(mode="json") for comment in run.comments.values()],
        }
        for kind, items in collections.items():
            await self._sync_collection(run.run_id, kind, items, state)

    async def emit_event(self, event: EventRecord) -> None:
        await self._call(
            "runs:appendEvent",
            {"event": event.model_dump(mode="json"), "syncToken": self.sync_token},
        )

    async def reset(self) -> None:
        self._state.clear()
        await self._call(
            "runs:resetAll",
            {"syncToken": self.sync_token},
        )

    async def _sync_collection(
        self,
        run_id: str,
        kind: str,
        items: list[dict[str, Any]],
        state: _RunSyncState,
    ) -> None:
        id_field = self._id_field(kind)
        previous = state.item_hashes[kind]
        current_hashes: dict[str, str] = {}
        changed: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item[id_field])
            digest = self._hash(item)
            current_hashes[item_id] = digest
            if previous.get(item_id) != digest:
                changed.append(item)
        removed_ids = sorted(set(previous) - set(current_hashes))

        for chunk in self._chunk_items(changed):
            await self._call(
                "runs:syncRunChunk",
                {
                    "runId": run_id,
                    "kind": kind,
                    "items": chunk,
                    "removedIds": [],
                    "syncToken": self.sync_token,
                },
            )

        if removed_ids:
            for chunk in self._chunk_removed_ids(removed_ids):
                await self._call(
                    "runs:syncRunChunk",
                    {
                        "runId": run_id,
                        "kind": kind,
                        "items": [],
                        "removedIds": chunk,
                        "syncToken": self.sync_token,
                    },
                )

        state.item_hashes[kind] = current_hashes

    def _build_summary(self, run: RunState) -> dict[str, Any]:
        agents = sorted(
            run.agents.values(),
            key=lambda agent: agent.best_rating_score,
            reverse=True,
        )
        bots = sorted(
            run.bots.values(),
            key=lambda bot: bot.rating_score,
            reverse=True,
        )
        best_agent = agents[0] if agents else None
        best_rating = best_agent.best_rating_score if best_agent else None
        return {
            "runId": run.run_id,
            "name": run.config.name,
            "description": run.config.description,
            "status": run.status.value,
            "createdAt": int(run.created_at.timestamp() * 1000),
            "startedAt": int(run.started_at.timestamp() * 1000) if run.started_at else None,
            "finishedAt": int(run.finished_at.timestamp() * 1000) if run.finished_at else None,
            "updatedAt": int(datetime.now(tz=UTC).timestamp() * 1000),
            "agentCount": len(run.agents),
            "botCount": len(run.bots),
            "activeBotCount": sum(1 for bot in run.bots.values() if bot.active),
            "gameCount": len(run.games),
            "offerCount": len(run.offers),
            "purchaseCount": len(run.purchases),
            "reviewCount": len(run.reviews),
            "bestAgentId": best_agent.agent_id if best_agent else None,
            "bestRating": best_rating,
            "bestElo": best_rating,
            "config": run.config.model_dump(mode="json"),
            "finalScores": run.final_scores,
            "payouts": run.payouts,
            "snapshotPayload": {
                "leaderboard": {
                    "agents": [
                        {
                            "agentId": agent.agent_id,
                            "bestBotId": agent.best_bot_id,
                            "bestRating": agent.best_rating_score,
                            "status": agent.status,
                        }
                        for agent in agents[:SNAPSHOT_TOP_AGENTS]
                    ],
                    "bots": [
                        {
                            "botId": bot.bot_id,
                            "agentId": bot.agent_id,
                            "rating": bot.rating_score,
                            "active": bot.active,
                            "name": bot.name,
                        }
                        for bot in bots[:SNAPSHOT_TOP_BOTS]
                    ],
                },
                "counts": {
                    "agentCount": len(run.agents),
                    "botCount": len(run.bots),
                    "activeBotCount": sum(1 for bot in run.bots.values() if bot.active),
                    "gameCount": len(run.games),
                    "offerCount": len(run.offers),
                    "purchaseCount": len(run.purchases),
                    "reviewCount": len(run.reviews),
                },
            },
        }

    def _compact_game_payload(self, game) -> dict[str, Any]:
        payload = game.model_dump(mode="json")
        payload["actions"] = []
        return payload

    def _id_field(self, kind: str) -> str:
        return {
            "agents": "agent_id",
            "bots": "bot_id",
            "games": "game_id",
            "offers": "offer_id",
            "purchases": "purchase_id",
            "reviews": "review_id",
            "comments": "message_id",
        }[kind]

    def _hash(self, item: dict[str, Any]) -> str:
        digest = hashlib.sha1(canonical_dumps(item))
        return digest.hexdigest()

    def _chunk_items(self, items: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_bytes = 2
        for item in items:
            size = len(dumps(item)) + 1
            if current and current_bytes + size > MAX_MUTATION_BYTES:
                chunks.append(current)
                current = []
                current_bytes = 2
            current.append(item)
            current_bytes += size
        if current:
            chunks.append(current)
        return chunks

    def _chunk_removed_ids(self, removed_ids: list[str]) -> list[list[str]]:
        chunks: list[list[str]] = []
        current: list[str] = []
        current_bytes = 2
        for item_id in removed_ids:
            size = len(item_id.encode("utf-8")) + 1
            if current and current_bytes + size > MAX_MUTATION_BYTES:
                chunks.append(current)
                current = []
                current_bytes = 2
            current.append(item_id)
            current_bytes += size
        if current:
            chunks.append(current)
        return chunks

    async def _call(self, name: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            try:
                await asyncio.to_thread(self.client.mutation, name, payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("convex sync failed for %s: %s", name, exc)


def build_convex_sync() -> ConvexSync | None:
    if not settings.convex_url:
        return None
    return ConvexSync(settings.convex_url, settings.convex_sync_token)
