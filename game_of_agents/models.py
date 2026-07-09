from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from game_of_agents.config_defaults import default_prompt_value


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


BASE_TRUESKILL_MU = 25.0
BASE_TRUESKILL_SIGMA = 25.0 / 3.0
RATING_DISPLAY_BASE = 1000.0
RATING_DISPLAY_SCALE = 40.0

DEFAULT_INITIAL_PROMPT_TEMPLATE = default_prompt_value("initial_prompt_template")
DEFAULT_CONTINUE_PROMPT_TEMPLATE = default_prompt_value("continue_prompt_template")
DEFAULT_WARNING_PROMPT_TEMPLATE = default_prompt_value("warning_prompt_template")
DEFAULT_WORKSPACE_README_TEMPLATE = default_prompt_value("workspace_readme_template")
DEFAULT_WORKSPACE_RULES_TEMPLATE = default_prompt_value("workspace_rules_template")
DEFAULT_POKERKIT_GUIDE = default_prompt_value("pokerkit_guide")
DEFAULT_POKER_RUNTIME_GUIDE = default_prompt_value("poker_runtime_guide")


def display_rating(score: float) -> float:
    return RATING_DISPLAY_BASE + RATING_DISPLAY_SCALE * score


class SettlementMode(str, Enum):
    NET = "net"
    ADDITIVE = "additive"


class AgentRuntime(str, Enum):
    MOCK = "mock"
    CODEX = "codex"
    CLAUDE = "claude"
    GEMINI = "gemini"
    OPENCODE = "opencode"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    FINISHED = "finished"
    FAILED = "failed"


class GameStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    FINISHED = "finished"
    FORFEIT = "forfeit"


class MatchFormat(str, Enum):
    FREEZEOUT = "freezeout"


class RatingSystem(str, Enum):
    TRUESKILL2 = "trueskill2"


class LeaderboardScore(str, Enum):
    CONSERVATIVE = "conservative"
    MU = "mu"


class CommentFeedMode(str, Enum):
    PER_AGENT = "per_agent"


class CommentFeedRuntime(str, Enum):
    MOCK = "mock"
    ANTHROPIC = "anthropic"


class ConversationTurnKind(str, Enum):
    PROMPT = "prompt"
    RESPONSE = "response"
    WARNING = "warning"
    ERROR = "error"


class AgentConfig(BaseModel):
    agent_id: str
    model: str | None = None
    runtime: AgentRuntime = AgentRuntime.MOCK
    internet_access: bool = False
    prompt: str = "Build the best poker bot you can."
    command: list[str] = Field(default_factory=list)
    initial_prompt_template: str | None = None
    continue_prompt_template: str | None = None
    warning_prompt_template: str | None = None
    workspace_readme_template: str | None = None
    workspace_rules_template: str | None = None


class GameConfig(BaseModel):
    variant_id: str = "poker.nlhe"
    match_format: MatchFormat = MatchFormat.FREEZEOUT
    players_per_match: int = 4
    starting_stack: int = 100
    small_blind: int = 1
    big_blind: int = 2
    ante: int = 0
    min_bet: int = 2
    max_rounds_per_match: int = 100
    game_time_bank_seconds: float = 5.0
    action_increment_seconds: float = 0.0
    allow_same_agent_table: bool = False
    max_concurrent_matches_per_bot: int | None = None


class RatingConfig(BaseModel):
    system: RatingSystem = RatingSystem.TRUESKILL2
    matchmaking_spread: float = 8.0
    beta: float = BASE_TRUESKILL_MU / 6.0
    tau: float = BASE_TRUESKILL_MU / 300.0
    draw_probability: float = 0.0
    leaderboard_score: LeaderboardScore = LeaderboardScore.CONSERVATIVE


class CommentFeedConfig(BaseModel):
    enabled: bool = False
    interval_minutes: int | None = 5
    interval_seconds: int | None = None
    mode: CommentFeedMode = CommentFeedMode.PER_AGENT
    runtime: CommentFeedRuntime = CommentFeedRuntime.ANTHROPIC
    model: str | None = None
    history_turn_limit: int = 8
    feed_context_limit: int = 20
    max_chars: int = 280

    @property
    def cadence_seconds(self) -> int:
        if self.interval_seconds is not None:
            return self.interval_seconds
        return int((self.interval_minutes or 0) * 60)


class RunConfig(BaseModel):
    name: str
    description: str
    duration_minutes: int = 60
    last_warning_minutes: int = 10
    convergence_tail_minutes: int = 5
    settlement_mode: SettlementMode = SettlementMode.NET
    max_active_bots_per_agent: int = 3
    bot_failure_retirement_threshold: int = 5
    concurrent_matches: int = 8
    capture_actions: bool = False
    artifact_bundle_max_bytes: int = 512_000
    soft_kill_grace_seconds: int = 15
    agent_poll_seconds: float = 2.0
    tournament_poll_seconds: float = 0.25
    agent_sandbox_cpu: int = 2
    tournament_cpu: int = 4
    marketplace_enabled: bool = True
    chat_enabled: bool = True
    chat_allowed_reactions: list[str] = Field(
        default_factory=lambda: ["thumbs_up", "thumbs_down", "fire", "laugh", "eyes", "heart"]
    )
    initial_prompt_template: str = DEFAULT_INITIAL_PROMPT_TEMPLATE
    continue_prompt_template: str = DEFAULT_CONTINUE_PROMPT_TEMPLATE
    warning_prompt_template: str = DEFAULT_WARNING_PROMPT_TEMPLATE
    workspace_readme_template: str = DEFAULT_WORKSPACE_README_TEMPLATE
    workspace_rules_template: str = DEFAULT_WORKSPACE_RULES_TEMPLATE
    pokerkit_guide: str = DEFAULT_POKERKIT_GUIDE
    poker_runtime_guide: str = DEFAULT_POKER_RUNTIME_GUIDE
    match_executor: Literal["process", "thread"] = "process"
    match_worker_processes: int | None = None
    match_worker_threads: int | None = None
    runner_cpu: int | None = None
    controller_cpu: int | None = None
    agents: list[AgentConfig]
    game: GameConfig = Field(default_factory=GameConfig)
    rating: RatingConfig = Field(default_factory=RatingConfig)
    comment_feed: CommentFeedConfig = Field(default_factory=CommentFeedConfig)

    # Deprecated compatibility aliases retained for one version.
    elo_spread: float | None = None
    game_time_bank_seconds: float | None = None
    action_increment_seconds: float | None = None

    @model_validator(mode="before")
    @classmethod
    def apply_legacy_aliases(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        game = dict(data.get("game") or {})
        rating = dict(data.get("rating") or {})
        if "game_time_bank_seconds" in data and "game_time_bank_seconds" not in game:
            game["game_time_bank_seconds"] = data["game_time_bank_seconds"]
        if "action_increment_seconds" in data and "action_increment_seconds" not in game:
            game["action_increment_seconds"] = data["action_increment_seconds"]
        if "elo_spread" in data and "matchmaking_spread" not in rating:
            rating["matchmaking_spread"] = float(data["elo_spread"]) / RATING_DISPLAY_SCALE
        if "match_worker_threads" in data and "match_worker_processes" not in data:
            data["match_worker_processes"] = data["match_worker_threads"]
        if "runner_cpu" in data and "controller_cpu" not in data:
            data["controller_cpu"] = data["runner_cpu"]
        if game:
            data["game"] = game
        if rating:
            data["rating"] = rating
        return data

    @model_validator(mode="after")
    def validate_values(self) -> RunConfig:
        if self.duration_minutes <= 0 or self.last_warning_minutes <= 0:
            raise ValueError("minutes must be positive")
        if self.convergence_tail_minutes < 0:
            raise ValueError("convergence_tail_minutes must be non-negative")
        if (
            self.max_active_bots_per_agent <= 0
            or self.bot_failure_retirement_threshold <= 0
            or self.concurrent_matches <= 0
        ):
            raise ValueError("counts must be positive")
        if self.artifact_bundle_max_bytes <= 0:
            raise ValueError("artifact_bundle_max_bytes must be positive")
        if self.soft_kill_grace_seconds <= 0:
            raise ValueError("soft_kill_grace_seconds must be positive")
        if self.agent_poll_seconds <= 0 or self.tournament_poll_seconds <= 0:
            raise ValueError("poll intervals must be positive")
        if self.agent_sandbox_cpu <= 0 or self.tournament_cpu <= 0:
            raise ValueError("sandbox cpu counts must be positive")
        if self.match_worker_processes is not None and self.match_worker_processes <= 0:
            raise ValueError("match_worker_processes must be positive")
        if self.match_worker_threads is not None and self.match_worker_threads <= 0:
            raise ValueError("match_worker_threads must be positive")
        if self.runner_cpu is not None and self.runner_cpu <= 0:
            raise ValueError("runner_cpu must be positive")
        if self.controller_cpu is not None and self.controller_cpu <= 0:
            raise ValueError("controller_cpu must be positive")
        if self.agents:
            self.game.players_per_match = max(2, min(self.game.players_per_match, len(self.agents)))
        if self.game.players_per_match < 2:
            raise ValueError("players_per_match must be at least 2")
        if self.game.max_rounds_per_match <= 0:
            raise ValueError("max_rounds_per_match must be positive")
        if (
            self.game.max_concurrent_matches_per_bot is not None
            and self.game.max_concurrent_matches_per_bot <= 0
        ):
            raise ValueError("max_concurrent_matches_per_bot must be positive")
        if self.comment_feed.cadence_seconds <= 0:
            raise ValueError("comment interval must be positive")
        if self.comment_feed.max_chars <= 0:
            raise ValueError("comment max chars must be positive")
        if self.comment_feed.enabled and not self.chat_enabled:
            raise ValueError("comment_feed.enabled requires chat_enabled")
        if not self.chat_allowed_reactions:
            raise ValueError("chat_allowed_reactions must not be empty")
        return self

    @field_validator(
        "initial_prompt_template",
        "continue_prompt_template",
        "warning_prompt_template",
        "workspace_readme_template",
        "workspace_rules_template",
        "pokerkit_guide",
        "poker_runtime_guide",
    )
    @classmethod
    def non_empty_templates(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string in the run config")
        return value


class AgentState(BaseModel):
    agent_id: str
    runtime: AgentRuntime
    internet_access: bool
    workspace: str
    sandbox_name: str | None = None
    best_bot_id: str | None = None
    best_rating_mu: float = BASE_TRUESKILL_MU
    best_rating_sigma: float = BASE_TRUESKILL_SIGMA
    best_rating_score: float = 0.0
    best_elo: float = RATING_DISPLAY_BASE
    status: Literal["idle", "running", "finished", "failed"] = "idle"
    last_message: str | None = None
    sandbox_id: str | None = None
    sandbox_status: str | None = None
    current_step_started_at: datetime | None = None
    last_activity_at: datetime | None = None
    last_output_at: datetime | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class BotArtifact(BaseModel):
    path: str
    content: str


class BotSubmission(BaseModel):
    bot_id: str = Field(default_factory=lambda: new_id("bot"))
    agent_id: str
    name: str
    description: str
    entrypoint: str
    module_path: str
    bundle_storage_id: str | None = None
    bundle_bytes: int | None = None
    file_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    active: bool = True
    matches_played: int = 0
    failure_count: int = 0
    last_failure_reason: str | None = None
    retired_reason: str | None = None
    rating_mu: float = BASE_TRUESKILL_MU
    rating_sigma: float = BASE_TRUESKILL_SIGMA
    rating_score: float = 0.0
    elo: float = RATING_DISPLAY_BASE
    artifacts: list[BotArtifact] = Field(default_factory=list)


class BotSubmissionRequest(BaseModel):
    agent_id: str
    name: str
    description: str
    entrypoint: str
    module_path: str
    artifacts: list[BotArtifact] = Field(default_factory=list)


class Offer(BaseModel):
    offer_id: str = Field(default_factory=lambda: new_id("offer"))
    seller_agent_id: str
    bot_id: str
    title: str
    description: str
    evidence: str
    price_pct: float
    artifact_paths: list[str]
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    review_count: int = 0


class OfferCreateRequest(BaseModel):
    seller_agent_id: str
    bot_id: str
    title: str
    description: str
    evidence: str
    price_pct: float
    artifact_paths: list[str]


class OfferUpdateRequest(BaseModel):
    price_pct: float


class Purchase(BaseModel):
    purchase_id: str = Field(default_factory=lambda: new_id("purchase"))
    offer_id: str
    buyer_agent_id: str
    seller_agent_id: str
    price_pct: float
    created_at: datetime = Field(default_factory=utcnow)


class PurchaseRequest(BaseModel):
    buyer_agent_id: str


class Review(BaseModel):
    review_id: str = Field(default_factory=lambda: new_id("review"))
    offer_id: str
    buyer_agent_id: str
    text: str
    created_at: datetime = Field(default_factory=utcnow)


class ReviewRequest(BaseModel):
    buyer_agent_id: str
    text: str


class RunMarketplaceAnalysisSummary(BaseModel):
    totalOffers: int
    totalPurchases: int
    totalReviews: int
    avgPricePct: float
    mostActiveSeller: str | None = None
    mostActiveBuyer: str | None = None
    sameModelPurchasePct: float | None = None


class RunAnalysisSummary(BaseModel):
    runId: str
    status: str
    updatedAt: int
    giniCoefficient: float
    avgAggression: float
    botsSubmitted: int
    chatMessageCount: int
    topAgentElo: float | None = None
    marketplace: RunMarketplaceAnalysisSummary


class MatchParticipantResult(BaseModel):
    bot_id: str
    agent_id: str
    seat: int
    placement: int
    ending_chips: int
    eliminated_round: int | None = None


class MatchResult(BaseModel):
    game_id: str = Field(default_factory=lambda: new_id("game"))
    run_id: str
    status: GameStatus = GameStatus.PENDING
    table_size: int = 2
    round_count: int = 0
    max_rounds_reached: bool = False
    participants: list[MatchParticipantResult] = Field(default_factory=list)
    winner_bot_id: str | None = None
    loser_bot_id: str | None = None
    reason: str | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    # Deprecated two-player compatibility fields.
    bot_a_id: str | None = None
    bot_b_id: str | None = None
    agent_a_id: str | None = None
    agent_b_id: str | None = None


class EventRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    run_id: str
    kind: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utcnow)


class CommentMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: new_id("cmt"))
    run_id: str
    author_agent_id: str
    commentator_id: str
    text: str
    created_at: datetime = Field(default_factory=utcnow)
    sequence: int = 0
    parent_message_id: str | None = None


class CommentPostRequest(BaseModel):
    author_agent_id: str
    commentator_id: str
    text: str
    parent_message_id: str | None = None


class AgentSteerRequest(BaseModel):
    text: str


class ConversationTurn(BaseModel):
    turn_id: str = Field(default_factory=lambda: new_id("turn"))
    run_id: str
    agent_id: str
    kind: ConversationTurnKind
    text: str
    step_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AgentConversationBlock(BaseModel):
    block_id: str
    run_id: str
    agent_id: str
    step_id: str | None = None
    role: Literal["user", "assistant", "tool", "system"] = "assistant"
    kind: Literal["prompt", "text", "tool", "warning", "summary", "error"] = "text"
    title: str
    text: str
    collapsed: bool = False
    streaming: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RunState(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    config: RunConfig
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    agents: dict[str, AgentState] = Field(default_factory=dict)
    bots: dict[str, BotSubmission] = Field(default_factory=dict)
    offers: dict[str, Offer] = Field(default_factory=dict)
    purchases: dict[str, Purchase] = Field(default_factory=dict)
    reviews: dict[str, Review] = Field(default_factory=dict)
    games: dict[str, MatchResult] = Field(default_factory=dict)
    comments: dict[str, CommentMessage] = Field(default_factory=dict)
    transcripts: dict[str, list[ConversationTurn]] = Field(default_factory=dict)
    final_scores: dict[str, float] = Field(default_factory=dict)
    payouts: dict[str, float] = Field(default_factory=dict)
    controller_sandbox_id: str | None = None
    controller_status: str | None = None
    controller_last_seen_at: datetime | None = None
    last_error: str | None = None

    def path(self, root: Path) -> Path:
        return root / f"{self.run_id}.json"
