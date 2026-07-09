from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status

from game_of_agents.events import EventSink
from game_of_agents.models import (
    EventRecord,
    Offer,
    OfferCreateRequest,
    OfferUpdateRequest,
    Purchase,
    PurchaseRequest,
    Review,
    ReviewRequest,
)
from game_of_agents.store import RunStore
from game_of_agents.workspaces import WorkspaceManager


class MarketplaceService:
    def __init__(self, store: RunStore, events: EventSink, workspaces: WorkspaceManager) -> None:
        self.store = store
        self.events = events
        self.workspaces = workspaces

    async def list_offers(self, run_id: str) -> list[Offer]:
        run = await self._require_run(run_id)
        return sorted(run.offers.values(), key=lambda offer: offer.updated_at, reverse=True)

    async def create_offer(self, run_id: str, request: OfferCreateRequest) -> Offer:
        run = await self._require_run(run_id)
        if request.seller_agent_id not in run.agents:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="seller not found")
        bot = run.bots.get(request.bot_id)
        if bot is None or bot.agent_id != request.seller_agent_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="bot not found")
        seller = run.agents[request.seller_agent_id]
        self.workspaces.bot_artifacts(seller.workspace, request.artifact_paths)
        offer = Offer(
            seller_agent_id=request.seller_agent_id,
            bot_id=request.bot_id,
            title=request.title,
            description=request.description,
            evidence=request.evidence,
            price_pct=request.price_pct,
            artifact_paths=request.artifact_paths,
        )
        run.offers[offer.offer_id] = offer
        await self.store.save_run(run)
        await self.events.emit(
            EventRecord(run_id=run_id, kind="offer.created", payload=offer.model_dump(mode="json"))
        )
        return offer

    async def update_offer(self, run_id: str, offer_id: str, request: OfferUpdateRequest) -> Offer:
        run = await self._require_run(run_id)
        offer = run.offers.get(offer_id)
        if offer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
        offer.price_pct = request.price_pct
        offer.updated_at = datetime.now(tz=UTC)
        run.offers[offer.offer_id] = offer
        await self.store.save_run(run)
        await self.events.emit(
            EventRecord(
                run_id=run_id,
                kind="offer.updated",
                payload={"offer_id": offer_id, "price_pct": request.price_pct},
            )
        )
        return offer

    async def buy_offer(self, run_id: str, offer_id: str, request: PurchaseRequest) -> Purchase:
        run = await self._require_run(run_id)
        offer = run.offers.get(offer_id)
        if offer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
        if request.buyer_agent_id == offer.seller_agent_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="seller cannot buy own offer")
        if request.buyer_agent_id not in run.agents:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="buyer not found")
        duplicate = next(
            (
                purchase
                for purchase in run.purchases.values()
                if purchase.offer_id == offer_id and purchase.buyer_agent_id == request.buyer_agent_id
            ),
            None,
        )
        if duplicate is not None:
            return duplicate
        seller = run.agents[offer.seller_agent_id]
        buyer = run.agents[request.buyer_agent_id]
        copied = self.workspaces.materialize_offer(
            run_id,
            offer_id,
            seller.workspace,
            buyer.workspace,
            offer.artifact_paths,
        )
        purchase = Purchase(
            offer_id=offer_id,
            buyer_agent_id=request.buyer_agent_id,
            seller_agent_id=offer.seller_agent_id,
            price_pct=offer.price_pct,
        )
        run.purchases[purchase.purchase_id] = purchase
        await self.store.save_run(run)
        await self.events.emit(
            EventRecord(
                run_id=run_id,
                kind="offer.purchased",
                payload={
                    **purchase.model_dump(mode="json"),
                    "copied_paths": [artifact.path for artifact in copied],
                },
            )
        )
        return purchase

    async def add_review(self, run_id: str, request: ReviewRequest, offer_id: str) -> Review:
        run = await self._require_run(run_id)
        offer = run.offers.get(offer_id)
        if offer is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="offer not found")
        purchased = any(
            purchase.offer_id == offer_id and purchase.buyer_agent_id == request.buyer_agent_id
            for purchase in run.purchases.values()
        )
        if not purchased:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="buyer has not purchased offer")
        review = Review(offer_id=offer_id, buyer_agent_id=request.buyer_agent_id, text=request.text)
        run.reviews[review.review_id] = review
        offer.review_count += 1
        run.offers[offer.offer_id] = offer
        await self.store.save_run(run)
        await self.events.emit(
            EventRecord(run_id=run_id, kind="offer.reviewed", payload=review.model_dump(mode="json"))
        )
        return review

    async def _require_run(self, run_id: str):
        run = await self.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
        return run
