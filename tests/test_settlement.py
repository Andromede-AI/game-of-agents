from __future__ import annotations

from game_of_agents.models import Purchase, SettlementMode
from game_of_agents.settlement import compute_marketplace_payouts


def test_compute_marketplace_payouts_additive_100_percent() -> None:
    payouts = compute_marketplace_payouts(
        {"seller": 5.0, "buyer": 12.5},
        [Purchase(offer_id="offer_1", buyer_agent_id="buyer", seller_agent_id="seller", price_pct=100.0)],
        SettlementMode.ADDITIVE,
    )

    assert payouts == {"seller": 17.5, "buyer": 12.5}


def test_compute_marketplace_payouts_net_100_percent() -> None:
    payouts = compute_marketplace_payouts(
        {"seller": 5.0, "buyer": 12.5},
        [Purchase(offer_id="offer_1", buyer_agent_id="buyer", seller_agent_id="seller", price_pct=100.0)],
        SettlementMode.NET,
    )

    assert payouts == {"seller": 17.5, "buyer": 0.0}


def test_compute_marketplace_payouts_uses_purchase_snapshot_price() -> None:
    payouts = compute_marketplace_payouts(
        {"seller": 5.0, "buyer": 12.5},
        [
            {
                "offer_id": "offer_1",
                "buyer_agent_id": "buyer",
                "seller_agent_id": "seller",
                "price_pct": 6.0,
            }
        ],
        "additive",
    )

    assert payouts == {"seller": 5.75, "buyer": 12.5}
