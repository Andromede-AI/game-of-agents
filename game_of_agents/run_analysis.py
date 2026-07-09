from __future__ import annotations

from collections import Counter

from game_of_agents.models import (
    RunAnalysisSummary,
    RunMarketplaceAnalysisSummary,
    RunState,
)


def _millis(dt) -> int:
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def _gini_coefficient(values: list[float]) -> float:
    if not values or all(value == 0 for value in values):
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    cumsum = sum((2 * index - n + 1) * value for index, value in enumerate(sorted_vals))
    return cumsum / (n * sum(sorted_vals))


def compute_run_analysis_summary(run: RunState) -> RunAnalysisSummary:
    agent_models = {
        agent.agent_id: agent.model
        for agent in run.config.agents
    }
    agent_ids = sorted(run.agents.keys())
    action_counts = {
        agent_id: {"raises": 0, "passive": 0}
        for agent_id in agent_ids
    }

    for game in run.games.values():
        if game.status != "finished":
            continue
        seat_to_agent = {
            participant.seat: participant.agent_id
            for participant in game.participants
        }
        for action in game.actions:
            kind = action.get("kind")
            if kind not in {"raise_to", "check_call", "fold"}:
                continue
            seat = action.get("seat")
            agent_id = seat_to_agent.get(seat)
            if agent_id is None:
                continue
            if kind == "raise_to":
                action_counts.setdefault(agent_id, {"raises": 0, "passive": 0})["raises"] += 1
            else:
                action_counts.setdefault(agent_id, {"raises": 0, "passive": 0})["passive"] += 1

    aggression_values: list[float] = []
    for agent_id in agent_ids:
        counts = action_counts.get(agent_id, {"raises": 0, "passive": 0})
        passive = counts["passive"]
        aggression_values.append(counts["raises"] / passive if passive > 0 else 0.0)

    offer_counts = Counter(offer.seller_agent_id for offer in run.offers.values())
    purchase_counts = Counter(purchase.buyer_agent_id for purchase in run.purchases.values())

    same_model_purchases = 0
    comparable_purchases = 0
    for purchase in run.purchases.values():
        buyer_model = agent_models.get(purchase.buyer_agent_id)
        seller_model = agent_models.get(purchase.seller_agent_id)
        if not buyer_model or not seller_model:
            continue
        comparable_purchases += 1
        if buyer_model == seller_model:
            same_model_purchases += 1

    agent_elos = [float(agent.best_elo) for agent in run.agents.values()]
    avg_price_pct = (
        sum(float(offer.price_pct) for offer in run.offers.values()) / len(run.offers)
        if run.offers
        else 0.0
    )

    marketplace = RunMarketplaceAnalysisSummary(
        totalOffers=len(run.offers),
        totalPurchases=len(run.purchases),
        totalReviews=len(run.reviews),
        avgPricePct=avg_price_pct,
        mostActiveSeller=max(offer_counts, key=lambda agent_id: (offer_counts[agent_id], agent_id)) if offer_counts else None,
        mostActiveBuyer=max(purchase_counts, key=lambda agent_id: (purchase_counts[agent_id], agent_id)) if purchase_counts else None,
        sameModelPurchasePct=(same_model_purchases / comparable_purchases) * 100 if comparable_purchases > 0 else None,
    )

    updated_at = max(
        _millis(run.finished_at),
        _millis(run.started_at),
        _millis(run.created_at),
    )

    return RunAnalysisSummary(
        runId=run.run_id,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        updatedAt=updated_at,
        giniCoefficient=_gini_coefficient(agent_elos),
        avgAggression=sum(aggression_values) / len(aggression_values) if aggression_values else 0.0,
        botsSubmitted=len(run.bots),
        chatMessageCount=len(run.comments),
        topAgentElo=max(agent_elos) if agent_elos else None,
        marketplace=marketplace,
    )
