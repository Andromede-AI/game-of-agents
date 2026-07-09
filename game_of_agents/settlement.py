from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from game_of_agents.models import SettlementMode


def _purchase_field(purchase: Any, field: str) -> Any:
    if isinstance(purchase, dict):
        return purchase.get(field)
    return getattr(purchase, field, None)


def compute_marketplace_payouts(
    base_scores: Mapping[str, float],
    purchases: Iterable[Any],
    settlement_mode: SettlementMode | str,
) -> dict[str, float]:
    payouts = {agent_id: float(score) for agent_id, score in base_scores.items()}
    mode = settlement_mode.value if isinstance(settlement_mode, SettlementMode) else str(settlement_mode)
    for purchase in purchases:
        buyer = str(_purchase_field(purchase, "buyer_agent_id") or "")
        seller = str(_purchase_field(purchase, "seller_agent_id") or "")
        if not buyer or not seller:
            continue
        transfer = float(base_scores.get(buyer, 0.0)) * (float(_purchase_field(purchase, "price_pct") or 0.0) / 100.0)
        if mode == SettlementMode.NET.value:
            payouts[buyer] = payouts.get(buyer, float(base_scores.get(buyer, 0.0))) - transfer
        payouts[seller] = payouts.get(seller, float(base_scores.get(seller, 0.0))) + transfer
    return payouts
