"""Load and parse GoA run data for analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AgentRecord:
    agent_id: str
    runtime: str
    best_elo: float
    best_rating_mu: float
    best_rating_sigma: float
    best_bot_id: str | None
    status: str


@dataclass
class BotRecord:
    bot_id: str
    agent_id: str
    name: str
    elo: float
    rating_mu: float
    rating_sigma: float
    matches_played: int
    failure_count: int
    active: bool
    created_at: str


@dataclass
class GameAction:
    """A single player action within a match."""
    kind: str  # fold, check_call, raise_to
    amount: int | None
    round_index: int
    seat: int
    local_seat: int
    bot_id: str | None = None  # resolved from participant seat mapping
    agent_id: str | None = None


@dataclass
class GameRecord:
    game_id: str
    run_id: str
    status: str  # finished, forfeit
    table_size: int
    round_count: int
    participants: list[dict[str, Any]]
    winner_bot_id: str | None
    reason: str | None
    actions: list[dict[str, Any]]
    duration_seconds: float | None
    started_at: str | None
    finished_at: str | None

    @property
    def player_actions(self) -> list[GameAction]:
        """Extract only player actions (not round_start/deal_board)."""
        seat_to_bot = {p["seat"]: p["bot_id"] for p in self.participants}
        seat_to_agent = {p["seat"]: p["agent_id"] for p in self.participants}
        result = []
        for a in self.actions:
            if "kind" in a:
                result.append(GameAction(
                    kind=a["kind"],
                    amount=a.get("amount"),
                    round_index=a["round_index"],
                    seat=a["seat"],
                    local_seat=a.get("local_seat", a["seat"]),
                    bot_id=seat_to_bot.get(a["seat"]),
                    agent_id=seat_to_agent.get(a["seat"]),
                ))
        return result

    def actions_by_street(self) -> dict[str, list[GameAction]]:
        """Group player actions by street (preflop/flop/turn/river) per round."""
        # Track deal_board events to determine street boundaries
        round_deals: dict[int, int] = {}  # round_index -> count of deal_board events seen
        street_names = {0: "preflop", 1: "flop", 2: "turn", 3: "river"}
        seat_to_bot = {p["seat"]: p["bot_id"] for p in self.participants}
        seat_to_agent = {p["seat"]: p["agent_id"] for p in self.participants}

        result: dict[str, list[GameAction]] = {
            "preflop": [], "flop": [], "turn": [], "river": [],
        }
        for a in self.actions:
            ri = a.get("round_index", 0)
            if a.get("type") == "deal_board":
                round_deals[ri] = round_deals.get(ri, 0) + 1
            elif "kind" in a:
                deals_so_far = round_deals.get(ri, 0)
                street = street_names.get(deals_so_far, "river")
                result[street].append(GameAction(
                    kind=a["kind"],
                    amount=a.get("amount"),
                    round_index=ri,
                    seat=a["seat"],
                    local_seat=a.get("local_seat", a["seat"]),
                    bot_id=seat_to_bot.get(a["seat"]),
                    agent_id=seat_to_agent.get(a["seat"]),
                ))
        return result


@dataclass
class OfferRecord:
    offer_id: str
    seller_agent_id: str
    title: str
    price_pct: float
    artifact_paths: list[str]
    review_count: int
    created_at: str


@dataclass
class PurchaseRecord:
    purchase_id: str
    offer_id: str
    buyer_agent_id: str
    seller_agent_id: str
    price_pct: float
    created_at: str


@dataclass
class CommentRecord:
    message_id: str
    author_agent_id: str
    text: str
    sequence: int
    parent_message_id: str | None
    created_at: str


@dataclass
class EventRecord:
    event_id: str
    kind: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class RunData:
    """Parsed run data ready for analysis."""
    run_id: str
    config: dict[str, Any]
    status: str
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    agents: list[AgentRecord]
    bots: list[BotRecord]
    games: list[GameRecord]
    offers: list[OfferRecord]
    purchases: list[PurchaseRecord]
    comments: list[CommentRecord]
    transcripts: dict[str, list[dict[str, Any]]]
    final_scores: dict[str, float]
    payouts: dict[str, float]
    events: list[EventRecord] = field(default_factory=list)

    @property
    def finished_games(self) -> list[GameRecord]:
        return [g for g in self.games if g.status == "finished"]

    @property
    def agent_ids(self) -> list[str]:
        return [a.agent_id for a in self.agents]

    def agent_model(self, agent_id: str) -> str | None:
        """Get the model name for an agent from config."""
        for ac in self.config.get("agents", []):
            if ac.get("agent_id") == agent_id:
                return ac.get("model")
        return None

    def games_for_agent(self, agent_id: str) -> list[GameRecord]:
        return [
            g for g in self.finished_games
            if any(p["agent_id"] == agent_id for p in g.participants)
        ]

    def bot_to_agent(self) -> dict[str, str]:
        return {b.bot_id: b.agent_id for b in self.bots}


def _ts(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def load_run(path: str | Path) -> RunData:
    """Load a RunState JSON file into structured RunData."""
    path = Path(path)
    with open(path) as f:
        raw = json.load(f)

    agents = [
        AgentRecord(
            agent_id=aid,
            runtime=a.get("runtime", "unknown"),
            best_elo=float(a.get("best_elo", 0)),
            best_rating_mu=float(a.get("best_rating_mu", 25.0)),
            best_rating_sigma=float(a.get("best_rating_sigma", 8.33)),
            best_bot_id=a.get("best_bot_id"),
            status=a.get("status", "unknown"),
        )
        for aid, a in raw.get("agents", {}).items()
    ]

    bots = [
        BotRecord(
            bot_id=bid,
            agent_id=b["agent_id"],
            name=b.get("name", ""),
            elo=float(b.get("elo", 0)),
            rating_mu=float(b.get("rating_mu", 25.0)),
            rating_sigma=float(b.get("rating_sigma", 8.33)),
            matches_played=int(b.get("matches_played", 0)),
            failure_count=int(b.get("failure_count", 0)),
            active=bool(b.get("active", True)),
            created_at=_ts(b.get("created_at")),
        )
        for bid, b in raw.get("bots", {}).items()
    ]

    games = [
        GameRecord(
            game_id=gid,
            run_id=g.get("run_id", raw.get("run_id", "")),
            status=g.get("status", "unknown"),
            table_size=int(g.get("table_size", 2)),
            round_count=int(g.get("round_count", 0)),
            participants=g.get("participants", []),
            winner_bot_id=g.get("winner_bot_id"),
            reason=g.get("reason"),
            actions=g.get("actions", []),
            duration_seconds=g.get("duration_seconds"),
            started_at=_ts(g.get("started_at")),
            finished_at=_ts(g.get("finished_at")),
        )
        for gid, g in raw.get("games", {}).items()
    ]

    offers = [
        OfferRecord(
            offer_id=oid,
            seller_agent_id=o["seller_agent_id"],
            title=o.get("title", ""),
            price_pct=float(o.get("price_pct", 0)),
            artifact_paths=o.get("artifact_paths", []),
            review_count=int(o.get("review_count", 0)),
            created_at=_ts(o.get("created_at")),
        )
        for oid, o in raw.get("offers", {}).items()
    ]

    purchases = [
        PurchaseRecord(
            purchase_id=pid,
            offer_id=p["offer_id"],
            buyer_agent_id=p["buyer_agent_id"],
            seller_agent_id=p["seller_agent_id"],
            price_pct=float(p.get("price_pct", 0)),
            created_at=_ts(p.get("created_at")),
        )
        for pid, p in raw.get("purchases", {}).items()
    ]

    comments = [
        CommentRecord(
            message_id=cid,
            author_agent_id=c["author_agent_id"],
            text=c.get("text", ""),
            sequence=int(c.get("sequence", 0)),
            parent_message_id=c.get("parent_message_id"),
            created_at=_ts(c.get("created_at")),
        )
        for cid, c in raw.get("comments", {}).items()
    ]

    return RunData(
        run_id=raw.get("run_id", path.stem),
        config=raw.get("config", {}),
        status=raw.get("status", "unknown"),
        created_at=_ts(raw.get("created_at")),
        started_at=_ts(raw.get("started_at")),
        finished_at=_ts(raw.get("finished_at")),
        agents=agents,
        bots=bots,
        games=games,
        offers=offers,
        purchases=purchases,
        comments=comments,
        transcripts=raw.get("transcripts", {}),
        final_scores=raw.get("final_scores", {}),
        payouts=raw.get("payouts", {}),
    )


def load_events(path: str | Path) -> list[EventRecord]:
    """Load JSONL event stream."""
    path = Path(path)
    if not path.exists():
        return []
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            events.append(EventRecord(
                event_id=raw.get("event_id", ""),
                kind=raw.get("kind", ""),
                payload=raw.get("payload", {}),
                created_at=_ts(raw.get("created_at")),
            ))
    return events


def load_run_dir(data_dir: str | Path, run_id: str) -> RunData:
    """Load a run by ID from a data directory."""
    data_dir = Path(data_dir)
    run_path = data_dir / f"{run_id}.json"
    events_path = data_dir / "events" / f"{run_id}.jsonl"
    run = load_run(run_path)
    run.events = load_events(events_path)
    return run
