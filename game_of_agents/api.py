from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, status

from game_of_agents.agent_logs import list_agent_conversation
from game_of_agents.config_defaults import load_default_run_config
from game_of_agents.models import (
    AgentConversationBlock,
    AgentSteerRequest,
    BotSubmission,
    BotSubmissionRequest,
    CommentMessage,
    CommentPostRequest,
    Offer,
    OfferCreateRequest,
    OfferUpdateRequest,
    Purchase,
    PurchaseRequest,
    Review,
    ReviewRequest,
    RunAnalysisSummary,
    RunConfig,
    RunState,
)
from game_of_agents.orchestrator import Orchestrator
from game_of_agents.run_analysis import compute_run_analysis_summary
from game_of_agents.settings import settings


def require_token(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {settings.api_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


def create_app(
    read_hook: Callable[[], None] | None = None,
    write_hook: Callable[[], None] | None = None,
) -> FastAPI:
    orchestrator = Orchestrator(read_hook=read_hook, write_hook=write_hook)
    app = FastAPI(title="Game of Agents")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/default-run-config", dependencies=[Depends(require_token)])
    async def default_run_config() -> dict[str, object]:
        return load_default_run_config()

    @app.post("/reset", dependencies=[Depends(require_token)])
    async def reset() -> dict[str, str]:
        await orchestrator.reset()
        return {"status": "reset"}

    @app.post("/runs", dependencies=[Depends(require_token)], response_model=RunState)
    async def create_run(config: RunConfig) -> RunState:
        return await orchestrator.create_run(config)

    @app.get("/runs", dependencies=[Depends(require_token)], response_model=list[RunState])
    async def list_runs() -> list[RunState]:
        return await orchestrator.list_runs()

    @app.get("/runs/{run_id}", dependencies=[Depends(require_token)], response_model=RunState)
    async def get_run(run_id: str, full: bool = False) -> RunState:
        if orchestrator.distributed is not None:
            try:
                return await orchestrator.distributed.get_run(run_id, full=full)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
        return await orchestrator.get_run(run_id)

    @app.get("/runs/{run_id}/dashboard", dependencies=[Depends(require_token)])
    async def get_run_dashboard(
        run_id: str,
        snapshot_limit: int = 800,
        event_limit: int = 200,
        sample_full_history: bool = False,
        sample_size: int = 320,
    ) -> dict[str, Any]:
        if orchestrator.distributed is not None:
            try:
                return await orchestrator.distributed.get_run_dashboard(
                    run_id,
                    snapshot_limit=snapshot_limit,
                    event_limit=event_limit,
                    sample_full_history=sample_full_history,
                    sample_size=sample_size,
                )
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc

        run = await orchestrator.get_run(run_id)
        leaderboard_agents = sorted(
            [
                {
                    "agentId": agent.agent_id,
                    "bestBotId": agent.best_bot_id,
                    "bestRating": float(agent.best_rating_score),
                    "status": agent.status,
                }
                for agent in run.agents.values()
            ],
            key=lambda item: item["bestRating"],
            reverse=True,
        )
        leaderboard_bots = sorted(
            [
                {
                    "botId": bot.bot_id,
                    "agentId": bot.agent_id,
                    "name": bot.name,
                    "rating": float(bot.rating_score),
                    "active": bot.active,
                }
                for bot in run.bots.values()
            ],
            key=lambda item: item["rating"],
            reverse=True,
        )

        def _millis(value: datetime | None) -> int | None:
            if value is None:
                return None
            return int(value.astimezone(UTC).timestamp() * 1000)

        return {
            "run": {
                "runId": run.run_id,
                "name": run.config.name,
                "description": run.config.description,
                "status": run.status.value,
                "createdAt": _millis(run.created_at),
                "startedAt": _millis(run.started_at),
                "finishedAt": _millis(run.finished_at),
                "updatedAt": _millis(run.finished_at or run.started_at or run.created_at),
                "agentCount": len(run.agents),
                "botCount": len(run.bots),
                "activeBotCount": sum(1 for bot in run.bots.values() if bot.active),
                "gameCount": len(run.games),
                "offerCount": len(run.offers),
                "purchaseCount": len(run.purchases),
                "reviewCount": len(run.reviews),
                "bestAgentId": leaderboard_agents[0]["agentId"] if leaderboard_agents else None,
                "bestRating": leaderboard_agents[0]["bestRating"] if leaderboard_agents else None,
                "config": run.config.model_dump(mode="json"),
                "state": {
                    "agents": [agent.model_dump(mode="json") for agent in run.agents.values()],
                    "bots": [bot.model_dump(mode="json") for bot in run.bots.values()],
                    "games": [game.model_dump(mode="json") for game in run.games.values()],
                    "offers": [offer.model_dump(mode="json") for offer in run.offers.values()],
                    "purchases": [purchase.model_dump(mode="json") for purchase in run.purchases.values()],
                    "reviews": [review.model_dump(mode="json") for review in run.reviews.values()],
                    "comments": [message.model_dump(mode="json") for message in run.comments.values()],
                },
                "leaderboard": {
                    "agents": leaderboard_agents,
                    "bots": leaderboard_bots,
                },
                "finalScores": run.final_scores,
                "payouts": run.payouts,
            },
            "snapshots": [],
            "events": [],
            "comments": [],
            "feedMessages": [],
            "chatMessages": [],
        }

    @app.get("/runs/{run_id}/analysis", dependencies=[Depends(require_token)], response_model=RunAnalysisSummary)
    async def get_run_analysis(run_id: str) -> RunAnalysisSummary:
        if orchestrator.distributed is not None:
            try:
                payload = await orchestrator.distributed.get_run_analysis(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found") from exc
            return RunAnalysisSummary.model_validate(payload)

        run = await orchestrator.get_run(run_id)
        return compute_run_analysis_summary(run)

    @app.delete("/runs/{run_id}", dependencies=[Depends(require_token)])
    async def delete_run(run_id: str) -> dict[str, str]:
        await orchestrator.delete_run(run_id)
        return {"status": "deleted"}

    @app.post("/runs/{run_id}/start", dependencies=[Depends(require_token)], response_model=RunState)
    async def start_run(run_id: str) -> RunState:
        return await orchestrator.start_run(run_id)

    @app.post("/runs/{run_id}/stop", dependencies=[Depends(require_token)], response_model=RunState)
    async def stop_run(run_id: str) -> RunState:
        return await orchestrator.stop_run(run_id)

    @app.post("/runs/{run_id}/drain", dependencies=[Depends(require_token)], response_model=RunState)
    async def drain_run(run_id: str, iterations: int = 1) -> RunState:
        return await orchestrator.run_iterations(run_id, iterations)

    @app.get("/runs/{run_id}/leaderboard", dependencies=[Depends(require_token)])
    async def leaderboard(run_id: str) -> dict[str, object]:
        if orchestrator.distributed is not None:
            return await orchestrator.distributed.leaderboard(run_id)
        return await orchestrator.tournament.leaderboard(run_id)

    @app.get("/runs/{run_id}/games", dependencies=[Depends(require_token)])
    async def list_games(run_id: str) -> list[dict[str, object]]:
        if orchestrator.distributed is not None:
            return await orchestrator.distributed.list_games(run_id)
        return await orchestrator.tournament.list_games(run_id)

    @app.post("/runs/{run_id}/bots", dependencies=[Depends(require_token)], response_model=BotSubmission)
    async def submit_bot(run_id: str, request: BotSubmissionRequest) -> BotSubmission:
        return await orchestrator.tournament.submit_bot(run_id, request)

    @app.get("/runs/{run_id}/offers", dependencies=[Depends(require_token)], response_model=list[Offer])
    async def list_offers(run_id: str) -> list[Offer]:
        return await orchestrator.marketplace.list_offers(run_id)

    @app.post("/runs/{run_id}/offers", dependencies=[Depends(require_token)], response_model=Offer)
    async def create_offer(run_id: str, request: OfferCreateRequest) -> Offer:
        return await orchestrator.marketplace.create_offer(run_id, request)

    @app.patch("/runs/{run_id}/offers/{offer_id}", dependencies=[Depends(require_token)], response_model=Offer)
    async def update_offer(run_id: str, offer_id: str, request: OfferUpdateRequest) -> Offer:
        return await orchestrator.marketplace.update_offer(run_id, offer_id, request)

    @app.post("/runs/{run_id}/offers/{offer_id}/buy", dependencies=[Depends(require_token)], response_model=Purchase)
    async def buy_offer(run_id: str, offer_id: str, request: PurchaseRequest) -> Purchase:
        return await orchestrator.marketplace.buy_offer(run_id, offer_id, request)

    @app.post("/runs/{run_id}/offers/{offer_id}/reviews", dependencies=[Depends(require_token)], response_model=Review)
    async def review_offer(run_id: str, offer_id: str, review: ReviewRequest) -> Review:
        return await orchestrator.marketplace.add_review(run_id, review, offer_id)

    @app.get("/runs/{run_id}/comments", dependencies=[Depends(require_token)], response_model=list[CommentMessage])
    async def list_comments(run_id: str, limit: int = 50) -> list[CommentMessage]:
        if orchestrator.distributed is not None:
            payload = await orchestrator.distributed.list_comments(run_id, limit)
            return [CommentMessage.model_validate(item) for item in payload]
        return await orchestrator.comments.list_messages(run_id, limit)

    @app.get(
        "/runs/{run_id}/agents/{agent_id}/conversation",
        dependencies=[Depends(require_token)],
        response_model=list[AgentConversationBlock],
    )
    async def agent_conversation(
        run_id: str,
        agent_id: str,
        limit: int = 400,
    ) -> list[AgentConversationBlock]:
        if orchestrator.distributed is not None:
            return await orchestrator.distributed.get_agent_conversation(run_id, agent_id, limit)
        await orchestrator.get_run(run_id)
        return list_agent_conversation(run_id, agent_id, limit=limit)

    @app.post("/runs/{run_id}/agents/{agent_id}/steer", dependencies=[Depends(require_token)])
    async def steer_agent(
        run_id: str,
        agent_id: str,
        request: AgentSteerRequest,
    ) -> dict[str, object]:
        return await orchestrator.steer_agent(run_id, agent_id, request)

    @app.post("/runs/{run_id}/comments", dependencies=[Depends(require_token)], response_model=CommentMessage)
    async def post_comment(run_id: str, request: CommentPostRequest) -> CommentMessage:
        return await orchestrator.comments.post_message(run_id, request)

    return app
