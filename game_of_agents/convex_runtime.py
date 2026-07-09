from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import threading
import zipfile
from typing import Any, Iterable

import httpx
from convex import ConvexClient

from game_of_agents.models import (
    AgentConversationBlock,
    AgentState,
    BotArtifact,
    BotSubmission,
    CommentMessage,
    MatchResult,
    Offer,
    Purchase,
    Review,
    RunConfig,
    RunState,
    RunStatus,
)

MAX_CONVEX_MUTATION_BYTES = 512 * 1024
MAX_LOG_BLOCK_TEXT_CHARS = 16 * 1024


class ConvexRuntimeClient:
    def __init__(
        self,
        url: str,
        *,
        site_url: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        self.client = ConvexClient(url)
        self.site_url = site_url
        self.auth_token = auth_token
        self._client_lock = threading.RLock()

    def query(self, name: str, args: dict[str, Any] | None = None) -> Any:
        with self._client_lock:
            return self.client.query(name, _compact_args(args or {}))

    def mutation(self, name: str, args: dict[str, Any] | None = None) -> Any:
        payload = _compact_args(args or {})
        if self.auth_token:
            payload.setdefault("syncToken", self.auth_token)
        with self._client_lock:
            return self.client.mutation(name, payload)

    def create_run(self, config: RunConfig) -> str:
        result = self.mutation(
            "runtime:createRun",
            {"config": config.model_dump(mode="json")},
        )
        return str(result["runId"])

    def start_run(self, run_id: str) -> Any:
        return self.mutation("runtime:startRun", {"runId": run_id})

    def request_stop(self, run_id: str, *, grace_seconds: int) -> Any:
        return self.mutation(
            "runtime:requestStop",
            {"runId": run_id, "graceSeconds": grace_seconds},
        )

    def complete_run(self, run_id: str, *, final_scores: dict[str, float], payouts: dict[str, float]) -> Any:
        return self.mutation(
            "runtime:completeRun",
            {
                "runId": run_id,
                "finalScores": final_scores,
                "payouts": payouts,
            },
        )

    def fail_run(self, run_id: str, error: str) -> Any:
        return self.mutation("runtime:failRun", {"runId": run_id, "error": error})

    def set_read_only(self, run_id: str) -> Any:
        return self.mutation("runtime:setReadOnly", {"runId": run_id})

    def register_sandbox(
        self,
        run_id: str,
        *,
        role: str,
        sandbox_id: str,
        agent_id: str | None = None,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
        heartbeat_ttl_seconds: int | None = None,
    ) -> Any:
        return self.mutation(
            "runtime:registerSandbox",
            {
                "runId": run_id,
                "role": role,
                "sandboxId": sandbox_id,
                "agentId": agent_id,
                "status": status,
                "metadata": metadata,
                "heartbeatTtlSeconds": heartbeat_ttl_seconds,
            },
        )

    def heartbeat_sandbox(
        self,
        run_id: str,
        *,
        sandbox_id: str,
        status: str = "running",
        metadata_patch: dict[str, Any] | None = None,
        heartbeat_ttl_seconds: int | None = None,
    ) -> Any:
        return self.mutation(
            "runtime:heartbeatSandbox",
            {
                "runId": run_id,
                "sandboxId": sandbox_id,
                "status": status,
                "metadataPatch": metadata_patch,
                "heartbeatTtlSeconds": heartbeat_ttl_seconds,
            },
        )

    def finish_sandbox(
        self,
        run_id: str,
        *,
        sandbox_id: str,
        status: str,
        error: str | None = None,
    ) -> Any:
        return self.mutation(
            "runtime:finishSandbox",
            {"runId": run_id, "sandboxId": sandbox_id, "status": status, "error": error},
        )

    def list_sandboxes(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.query("runtime:listSandboxes", {"runId": run_id}) or [])

    def get_run_dashboard(
        self,
        run_id: str,
        *,
        snapshot_limit: int = 800,
        event_limit: int = 200,
    ) -> dict[str, Any] | None:
        payload = self.query(
            "runs:getRunDashboard",
            {"runId": run_id, "snapshotLimit": snapshot_limit, "eventLimit": event_limit},
        )
        if payload is None:
            return None
        state = dict(payload.get("run", {}).get("state") or {})
        for key, id_field in (
            ("agents", "agent_id"),
            ("bots", "bot_id"),
            ("games", "game_id"),
            ("offers", "offer_id"),
            ("purchases", "purchase_id"),
            ("reviews", "review_id"),
            ("comments", "message_id"),
        ):
            collection = state.get(key)
            if isinstance(collection, list):
                state[key] = {
                    str(item.get(id_field) or f"{key}-{index}"): item
                    for index, item in enumerate(collection)
                    if isinstance(item, dict)
                }
        payload["run"]["state"] = state
        return payload

    def get_run_dashboard_sampled(
        self,
        run_id: str,
        *,
        sample_size: int = 320,
        event_limit: int = 160,
    ) -> dict[str, Any] | None:
        payload = self.query(
            "runs:getRunDashboardSampled",
            {"runId": run_id, "sampleSize": sample_size, "eventLimit": event_limit},
        )
        if payload is None:
            return None
        state = dict(payload.get("run", {}).get("state") or {})
        for key, id_field in (
            ("agents", "agent_id"),
            ("bots", "bot_id"),
            ("games", "game_id"),
            ("offers", "offer_id"),
            ("purchases", "purchase_id"),
            ("reviews", "review_id"),
            ("comments", "message_id"),
        ):
            collection = state.get(key)
            if isinstance(collection, list):
                state[key] = {
                    str(item.get(id_field) or f"{key}-{index}"): item
                    for index, item in enumerate(collection)
                    if isinstance(item, dict)
                }
        payload["run"]["state"] = state
        return payload

    def get_run_analysis(self, run_id: str) -> dict[str, Any] | None:
        return self.query("runtime:getRunAnalysis", {"runId": run_id})

    def get_run_summary(self, run_id: str) -> dict[str, Any] | None:
        return self.query("runs:getRunSummary", {"runId": run_id})

    def get_agent_step_context(self, run_id: str, agent_id: str) -> dict[str, Any] | None:
        return self.query("runtime:getAgentStepContext", {"runId": run_id, "agentId": agent_id})

    def get_agent_analytics(self, run_id: str, agent_id: str) -> dict[str, Any] | None:
        return self.query("runtime:getAgentAnalytics", {"runId": run_id, "agentId": agent_id})

    def get_run_control(self, run_id: str) -> dict[str, Any] | None:
        return self.query("runtime:getRunControl", {"runId": run_id})

    def get_tournament_state(self, run_id: str) -> dict[str, Any] | None:
        return self.query("runtime:getTournamentState", {"runId": run_id})

    def list_runs(self) -> list[dict[str, Any]]:
        return list(self.query("runs:listRuns", {}))

    def delete_run(self, run_id: str, *, batch_size: int = 500) -> None:
        while True:
            result = self.mutation(
                "runs:deleteRunChunk",
                {"runId": run_id, "limit": batch_size},
            )
            if not result or not result.get("remaining", False):
                return

    def get_agent_conversation(self, run_id: str, agent_id: str, *, limit: int = 400) -> list[AgentConversationBlock]:
        payload = self.query(
            "runtime:getAgentConversation",
            {"runId": run_id, "agentId": agent_id, "limit": limit},
        )
        return [AgentConversationBlock.model_validate(item) for item in payload or []]

    def get_tournament_snapshot(self, run_id: str) -> dict[str, Any]:
        return dict(self.query("runtime:getTournamentSnapshot", {"runId": run_id}) or {})

    def query_games(
        self,
        run_id: str,
        *,
        limit: int = 25,
        scan_limit: int | None = None,
        order: str = "desc",
        agent_id: str | None = None,
        bot_id: str | None = None,
        winner_bot_id: str | None = None,
        status: str | None = None,
        reason_contains: str | None = None,
    ) -> dict[str, Any]:
        return dict(
            self.query(
                "runtime:queryGames",
                {
                    "runId": run_id,
                    "limit": limit,
                    "scanLimit": scan_limit,
                    "order": order,
                    "agentId": agent_id,
                    "botId": bot_id,
                    "winnerBotId": winner_bot_id,
                    "status": status,
                    "reasonContains": reason_contains,
                },
            )
            or {}
        )

    def list_marketplace_offers(self, run_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.query("runtime:listMarketplaceOffers", {"runId": run_id, "limit": limit}) or [])

    def get_offer_details(self, run_id: str, offer_id: str) -> dict[str, Any] | None:
        return self.query("runtime:getOfferDetails", {"runId": run_id, "offerId": offer_id})

    def list_pending_submissions(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.query("runtime:listPendingSubmissions", {"runId": run_id}) or [])

    def activate_submission(
        self,
        run_id: str,
        *,
        submission_id: str,
        bot: BotSubmission,
        evicted_bot_id: str | None = None,
    ) -> Any:
        return self.mutation(
            "runtime:activateSubmission",
            {
                "runId": run_id,
                "submissionId": submission_id,
                "bot": bot.model_dump(mode="json"),
                "evictedBotId": evicted_bot_id,
            },
        )

    def append_match_result(
        self,
        run_id: str,
        *,
        match: MatchResult,
        bots: Iterable[BotSubmission],
        agents: Iterable[AgentState],
    ) -> Any:
        return self.mutation(
            "runtime:appendMatchResult",
            {
                "runId": run_id,
                "match": match.model_dump(mode="json"),
                "bots": [bot.model_dump(mode="json") for bot in bots],
                "agents": [agent.model_dump(mode="json") for agent in agents],
            },
        )

    def append_match_results(
        self,
        run_id: str,
        *,
        matches: Iterable[MatchResult],
        bots: Iterable[BotSubmission],
        agents: Iterable[AgentState],
    ) -> Any:
        compact_matches = [_compact_match_payload(match) for match in matches]
        compact_bots = [_compact_bot_payload(bot) for bot in bots]
        compact_agents = [_compact_agent_payload(agent) for agent in agents]
        results: list[dict[str, Any]] = []
        for chunk in _chunk_array_argument(
            {"runId": run_id},
            "matches",
            compact_matches,
            max_bytes=MAX_CONVEX_MUTATION_BYTES,
        ):
            results.append(
                self.mutation(
                    "runtime:appendMatchSummaries",
                    {
                        "runId": run_id,
                        "matches": chunk,
                    },
                )
            )
        if compact_bots:
            for bot_chunk in _chunk_array_argument(
                {"runId": run_id, "agents": compact_agents},
                "bots",
                compact_bots,
                max_bytes=MAX_CONVEX_MUTATION_BYTES,
            ):
                results.append(
                    self.mutation(
                        "runtime:upsertTournamentState",
                        {
                            "runId": run_id,
                            "bots": bot_chunk,
                            "agents": compact_agents,
                        },
                    )
                )
        elif compact_agents:
            results.append(
                self.mutation(
                    "runtime:upsertTournamentState",
                    {
                        "runId": run_id,
                        "bots": [],
                        "agents": compact_agents,
                    },
                )
            )
        return results[-1] if results else {"matches": []}

    def append_log_blocks(
        self,
        run_id: str,
        *,
        agent_id: str,
        blocks: list[dict[str, Any]],
    ) -> Any:
        compact_blocks = [_compact_log_block(block) for block in blocks]
        if not compact_blocks:
            return []
        written: list[dict[str, Any]] = []
        for chunk in _chunk_array_argument(
            {"runId": run_id, "agentId": agent_id},
            "blocks",
            compact_blocks,
            max_bytes=MAX_CONVEX_MUTATION_BYTES,
        ):
            written.extend(
                self.mutation(
                    "runtime:appendLogBlocks",
                    {"runId": run_id, "agentId": agent_id, "blocks": chunk},
                )
                or []
            )
        return written

    def touch_agent_session(
        self,
        run_id: str,
        *,
        agent_id: str,
        status: str,
        last_message: str | None = None,
        best_bot_id: str | None = None,
        sandbox_id: str | None = None,
    ) -> Any:
        return self.mutation(
            "runtime:touchAgentSession",
            {
                "runId": run_id,
                "agentId": agent_id,
                "status": status,
                "lastMessage": last_message,
                "bestBotId": best_bot_id,
                "sandboxId": sandbox_id,
            },
        )

    def create_agent_steer(
        self,
        run_id: str,
        *,
        agent_id: str,
        text: str,
    ) -> Any:
        return self.mutation(
            "runtime:createAgentSteer",
            {
                "runId": run_id,
                "agentId": agent_id,
                "text": text,
            },
        )

    def claim_pending_agent_steers(
        self,
        run_id: str,
        *,
        agent_id: str,
    ) -> list[dict[str, Any]]:
        return list(
            self.mutation(
                "runtime:claimPendingAgentSteers",
                {
                    "runId": run_id,
                    "agentId": agent_id,
                },
            )
            or []
        )

    def post_comment(
        self,
        run_id: str,
        *,
        author_agent_id: str,
        commentator_id: str,
        text: str,
        parent_message_id: str | None = None,
    ) -> Any:
        return self.mutation(
            "runtime:postComment",
            {
                "runId": run_id,
                "authorAgentId": author_agent_id,
                "commentatorId": commentator_id,
                "text": text,
                "parentMessageId": parent_message_id,
            },
        )

    def react_comment(self, run_id: str, *, author_agent_id: str, message_id: str, emoji: str) -> Any:
        return self.mutation(
            "runtime:reactComment",
            {
                "runId": run_id,
                "authorAgentId": author_agent_id,
                "messageId": message_id,
                "emoji": emoji,
            },
        )

    def list_recent_messages(self, run_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.query("runtime:listRecentMessages", {"runId": run_id, "limit": limit}) or [])

    def create_offer(
        self,
        run_id: str,
        *,
        seller_agent_id: str,
        title: str,
        description: str,
        evidence: str,
        price_pct: float,
        bundle_storage_id: str,
        bundle_bytes: int,
        file_paths: list[str],
    ) -> Any:
        return self.mutation(
            "runtime:createOffer",
            {
                "runId": run_id,
                "sellerAgentId": seller_agent_id,
                "title": title,
                "description": description,
                "evidence": evidence,
                "pricePct": price_pct,
                "bundleStorageId": bundle_storage_id,
                "bundleBytes": bundle_bytes,
                "filePaths": file_paths,
            },
        )

    def update_offer(self, run_id: str, *, offer_id: str, price_pct: float) -> Any:
        return self.mutation(
            "runtime:updateOffer",
            {"runId": run_id, "offerId": offer_id, "pricePct": price_pct},
        )

    def purchase_offer(self, run_id: str, *, offer_id: str, buyer_agent_id: str) -> Any:
        return self.mutation(
            "runtime:purchaseOffer",
            {"runId": run_id, "offerId": offer_id, "buyerAgentId": buyer_agent_id},
        )

    def add_review(self, run_id: str, *, offer_id: str, buyer_agent_id: str, text: str) -> Any:
        return self.mutation(
            "runtime:addReview",
            {"runId": run_id, "offerId": offer_id, "buyerAgentId": buyer_agent_id, "text": text},
        )

    def register_bot_submission(
        self,
        run_id: str,
        *,
        agent_id: str,
        name: str,
        description: str,
        entrypoint: str,
        module_path: str,
        bundle_storage_id: str,
        bundle_bytes: int,
        file_paths: list[str],
    ) -> Any:
        return self.mutation(
            "runtime:registerBotSubmission",
            {
                "runId": run_id,
                "agentId": agent_id,
                "name": name,
                "description": description,
                "entrypoint": entrypoint,
                "modulePath": module_path,
                "bundleStorageId": bundle_storage_id,
                "bundleBytes": bundle_bytes,
                "filePaths": file_paths,
            },
        )

    def generate_upload_url(self) -> str:
        payload = self.mutation("runtime:generateUploadUrl", {})
        return str(payload["uploadUrl"])

    def get_download_url(self, storage_id: str) -> str:
        payload = self.query("runtime:getDownloadUrl", {"storageId": storage_id})
        return str(payload["url"])

    def upload_bundle(self, *, files: Iterable[Path], root: Path, max_total_bytes: int) -> dict[str, Any]:
        file_list = [path.resolve() for path in files]
        total_bytes = 0
        manifest: list[dict[str, Any]] = []
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in file_list:
                if not path.exists() or not path.is_file():
                    raise FileNotFoundError(path)
                relative = path.resolve().relative_to(root.resolve())
                content = path.read_bytes()
                total_bytes += len(content)
                if total_bytes > max_total_bytes:
                    raise ValueError(f"bundle exceeds max_total_bytes={max_total_bytes}")
                manifest.append({"path": str(relative), "bytes": len(content)})
                bundle.writestr(str(relative), content)
        upload_url = self.generate_upload_url()
        response = httpx.post(
            upload_url,
            content=archive.getvalue(),
            headers={"Content-Type": "application/zip"},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "storageId": payload["storageId"],
            "bundleBytes": len(archive.getvalue()),
            "totalFileBytes": total_bytes,
            "filePaths": [entry["path"] for entry in manifest],
            "manifest": manifest,
        }

    def download_bundle(self, storage_id: str, destination: Path) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        url = self.get_download_url(storage_id)
        response = httpx.get(url, timeout=60)
        response.raise_for_status()
        archive_bytes = io.BytesIO(response.content)
        written: list[Path] = []
        with zipfile.ZipFile(archive_bytes) as bundle:
            for member in bundle.infolist():
                target = destination / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(member.filename))
                written.append(target)
        return written

    def dashboard_to_run_state(self, dashboard: dict[str, Any]) -> RunState:
        run_payload = dashboard["run"]
        state_payload = dict(run_payload.get("state") or {})
        config = RunConfig.model_validate(run_payload["config"])
        run = RunState(
            run_id=str(run_payload["runId"]),
            config=config,
            status=RunStatus(str(run_payload["status"])),
            created_at=_ts_to_iso(run_payload["createdAt"]),
            started_at=_ts_to_iso_optional(run_payload.get("startedAt")),
            finished_at=_ts_to_iso_optional(run_payload.get("finishedAt")),
            final_scores={str(k): float(v) for k, v in dict(run_payload.get("finalScores") or {}).items()},
            payouts={str(k): float(v) for k, v in dict(run_payload.get("payouts") or {}).items()},
        )
        run.agents = {
            str(item["agent_id"]): AgentState.model_validate(item)
            for item in _collection_items(state_payload.get("agents"))
        }
        run.bots = {
            str(item["bot_id"]): BotSubmission.model_validate(item)
            for item in _collection_items(state_payload.get("bots"))
        }
        run.offers = {
            str(item["offer_id"]): Offer.model_validate(_normalize_offer_record(item))
            for item in _collection_items(state_payload.get("offers"))
        }
        run.purchases = {
            str(item["purchase_id"]): Purchase.model_validate(item)
            for item in _collection_items(state_payload.get("purchases"))
        }
        run.reviews = {
            str(item["review_id"]): Review.model_validate(item)
            for item in _collection_items(state_payload.get("reviews"))
        }
        run.games = {
            str(item["game_id"]): MatchResult.model_validate(item)
            for item in _collection_items(state_payload.get("games"))
        }
        run.comments = {
            str(item["message_id"]): CommentMessage.model_validate(item)
            for item in _collection_items(state_payload.get("comments"))
        }
        run.controller_sandbox_id = None
        run.controller_status = None
        run.controller_last_seen_at = None
        return run

    def summary_to_run_state(self, summary: dict[str, Any]) -> RunState:
        config_payload = summary.get("config")
        if not isinstance(config_payload, dict):
            config_payload = {
                "name": str(summary.get("name") or ""),
                "description": str(summary.get("description") or ""),
                "agents": [],
            }
        return RunState(
            run_id=str(summary["runId"]),
            config=RunConfig.model_validate(config_payload),
            status=RunStatus(str(summary["status"])),
            created_at=_ts_to_iso(summary["createdAt"]),
            started_at=_ts_to_iso_optional(summary.get("startedAt")),
            finished_at=_ts_to_iso_optional(summary.get("finishedAt")),
            final_scores={str(k): float(v) for k, v in dict(summary.get("finalScores") or {}).items()},
            payouts={str(k): float(v) for k, v in dict(summary.get("payouts") or {}).items()},
        )


def _ts_to_iso(value: int) -> Any:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(value / 1000, tz=_dt.UTC)


def _ts_to_iso_optional(value: int | None) -> Any:
    if value is None:
        return None
    return _ts_to_iso(value)


def _compact_args(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _json_size_bytes(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _chunk_array_argument(
    base_args: dict[str, Any],
    array_key: str,
    items: list[dict[str, Any]],
    *,
    max_bytes: int,
) -> list[list[dict[str, Any]]]:
    if not items:
        return []
    base_payload = dict(_compact_args(base_args))
    base_payload[array_key] = []
    base_size = _json_size_bytes(base_payload)
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = base_size
    for item in items:
        item_size = _json_size_bytes(item)
        separator_size = 1 if current else 0
        if current and current_size + separator_size + item_size > max_bytes:
            chunks.append(current)
            current = [item]
            current_size = base_size + item_size
            continue
        current.append(item)
        current_size += separator_size + item_size
    if current:
        chunks.append(current)
    return chunks


def _compact_match_payload(match: MatchResult) -> dict[str, Any]:
    return match.model_dump(mode="json")


def _compact_bot_payload(bot: BotSubmission) -> dict[str, Any]:
    payload = bot.model_dump(mode="json", exclude={"artifacts"})
    payload.pop("artifacts", None)
    return payload


def _compact_agent_payload(agent: AgentState) -> dict[str, Any]:
    return agent.model_dump(mode="json")


def _compact_log_block(block: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "block_id",
        "run_id",
        "agent_id",
        "sandbox_id",
        "step_id",
        "parent_block_id",
        "reply_to_log_id",
        "role",
        "kind",
        "sequence",
        "title",
        "text",
        "collapsed",
        "streaming",
        "created_at",
        "updated_at",
    }
    compacted = {
        key: value
        for key, value in block.items()
        if key in allowed_keys and value is not None
    }
    text = compacted.get("text")
    if isinstance(text, str) and len(text) > MAX_LOG_BLOCK_TEXT_CHARS:
        omitted = len(text) - MAX_LOG_BLOCK_TEXT_CHARS
        compacted["text"] = (
            text[:MAX_LOG_BLOCK_TEXT_CHARS]
            + f"\n…[truncated {omitted} chars for bandwidth]"
        )
    return compacted


def _collection_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    return []


def _normalize_offer_record(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    if "artifact_paths" not in payload:
        file_paths = payload.get("file_paths")
        if isinstance(file_paths, list):
            payload["artifact_paths"] = [str(path) for path in file_paths]
        else:
            payload["artifact_paths"] = []
    payload.setdefault("bot_id", "")
    return payload
