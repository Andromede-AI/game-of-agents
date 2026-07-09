from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from game_of_agents.events import JsonlEventSink
from game_of_agents.marketplace import MarketplaceService
from game_of_agents.models import (
    AgentConfig,
    AgentRuntime,
    BotSubmissionRequest,
    OfferCreateRequest,
    OfferUpdateRequest,
    PurchaseRequest,
    ReviewRequest,
    RunConfig,
    RunState,
)
from game_of_agents.store import RunStore
from game_of_agents.tournament import TournamentService
from game_of_agents.workspaces import WorkspaceManager


async def create_services(tmp_path: Path):
    store = RunStore(tmp_path / "runs")
    events = JsonlEventSink(tmp_path / "events")
    workspaces = WorkspaceManager(tmp_path / "workspaces")
    tournament = TournamentService(store, events, workspaces)
    marketplace = MarketplaceService(store, events, workspaces)
    run = RunState(
        config=RunConfig(
            name="market",
            description="market smoke",
            agents=[
                AgentConfig(agent_id="alpha", runtime=AgentRuntime.MOCK),
                AgentConfig(agent_id="beta", runtime=AgentRuntime.MOCK),
            ],
        )
    )
    for agent in run.config.agents:
        run.agents[agent.agent_id] = workspaces.scaffold_agent(run, agent)
    await store.save_run(run)

    alpha_root = Path(run.agents["alpha"].workspace)
    alpha_bot = alpha_root / "alpha_bot.py"
    alpha_bot.write_text(
        "\n".join(
            [
                "from game_of_agents.games.base import BotAction",
                "from game_of_agents.games.poker.bot import PokerBot, PokerObservation",
                "",
                "class AlphaBot(PokerBot):",
                "    def choose_action(self, observation: PokerObservation) -> BotAction:",
                "        if 'check_call' in observation.legal_actions:",
                "            return BotAction('check_call')",
                "        return BotAction('fold')",
            ]
        ),
        encoding="utf-8",
    )
    submission = await tournament.submit_bot(
        run.run_id,
        BotSubmissionRequest(
            agent_id="alpha",
            name="alpha-bot",
            description="seller bot",
            entrypoint="AlphaBot",
            module_path="alpha_bot.py",
        ),
    )
    return run, workspaces, marketplace, submission


@pytest.mark.asyncio
async def test_marketplace_purchase_copies_artifacts_and_allows_reviews(tmp_path: Path) -> None:
    run, _, marketplace, submission = await create_services(tmp_path)

    offer = await marketplace.create_offer(
        run.run_id,
        OfferCreateRequest(
            seller_agent_id="alpha",
            bot_id=submission.bot_id,
            title="preflop ideas",
            description="A tiny module",
            evidence="based on local smoke tests",
            price_pct=10.0,
            artifact_paths=["alpha_bot.py"],
        ),
    )
    offer = await marketplace.update_offer(run.run_id, offer.offer_id, OfferUpdateRequest(price_pct=12.5))
    purchase = await marketplace.buy_offer(run.run_id, offer.offer_id, PurchaseRequest(buyer_agent_id="beta"))

    beta_import = Path(run.agents["beta"].workspace) / "imports" / offer.offer_id / "alpha_bot.py"
    assert beta_import.exists()
    assert purchase.price_pct == 12.5

    review = await marketplace.add_review(
        run.run_id,
        ReviewRequest(buyer_agent_id="beta", text="Useful starting point"),
        offer.offer_id,
    )
    assert review.text == "Useful starting point"


@pytest.mark.asyncio
async def test_marketplace_purchase_keeps_price_at_purchase_time(tmp_path: Path) -> None:
    run, _, marketplace, submission = await create_services(tmp_path)

    offer = await marketplace.create_offer(
        run.run_id,
        OfferCreateRequest(
            seller_agent_id="alpha",
            bot_id=submission.bot_id,
            title="preflop ideas",
            description="A tiny module",
            evidence="based on local smoke tests",
            price_pct=6.0,
            artifact_paths=["alpha_bot.py"],
        ),
    )
    purchase = await marketplace.buy_offer(run.run_id, offer.offer_id, PurchaseRequest(buyer_agent_id="beta"))
    updated = await marketplace.update_offer(run.run_id, offer.offer_id, OfferUpdateRequest(price_pct=100.0))

    assert purchase.price_pct == 6.0
    assert updated.price_pct == 100.0


@pytest.mark.asyncio
async def test_non_buyer_cannot_review_offer(tmp_path: Path) -> None:
    run, _, marketplace, submission = await create_services(tmp_path)
    offer = await marketplace.create_offer(
        run.run_id,
        OfferCreateRequest(
            seller_agent_id="alpha",
            bot_id=submission.bot_id,
            title="tiny module",
            description="A tiny module",
            evidence="none",
            price_pct=5.0,
            artifact_paths=["alpha_bot.py"],
        ),
    )

    with pytest.raises(HTTPException) as error:
        await marketplace.add_review(
            run.run_id,
            ReviewRequest(buyer_agent_id="beta", text="I should not be allowed"),
            offer.offer_id,
        )

    assert error.value.status_code == 403
