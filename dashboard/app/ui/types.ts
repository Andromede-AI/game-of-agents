/* ── Data types for runs:getRunDashboard ── */

export type AgentConfig = {
  agent_id: string;
  model?: string | null;
  runtime: string;
  internet_access: boolean;
  prompt: string;
  command?: string[];
};

export type RunConfig = {
  name: string;
  description?: string;
  duration_minutes: number;
  last_warning_minutes?: number;
  settlement_mode: string;
  max_active_bots_per_agent: number;
  concurrent_matches: number;
  initial_prompt_template: string;
  continue_prompt_template: string;
  warning_prompt_template: string;
  workspace_readme_template: string;
  workspace_rules_template: string;
  pokerkit_guide: string;
  poker_runtime_guide: string;
  elo_spread?: number;
  game_time_bank_seconds?: number;
  action_increment_seconds?: number;
  game?: {
    match_format?: string;
    players_per_match?: number;
    allow_same_agent_table?: boolean;
    starting_stack?: number;
    small_blind?: number;
    big_blind?: number;
    ante?: number;
    min_bet?: number;
    max_rounds_per_match?: number | null;
    game_time_bank_seconds?: number;
    action_increment_seconds?: number;
  };
  rating?: {
    system?: string;
    matchmaking_spread?: number;
    leaderboard_score?: string;
  };
  comment_feed?: {
    enabled?: boolean;
    interval_minutes?: number;
    interval_seconds?: number;
    runtime?: string;
    max_chars?: number;
  };
  agents: AgentConfig[];
};

export type AgentState = {
  agent_id: string;
  status: string;
  best_bot_id: string | null;
  best_rating_score?: number | null;
  best_rating_mu?: number | null;
  best_rating_sigma?: number | null;
  best_rating?: number | null;
  best_elo?: number | null;
  last_message?: string | null;
  runtime?: string;
  internet_access?: boolean;
  workspace?: string;
};

export type BotState = {
  bot_id: string;
  agent_id: string;
  name: string;
  rating_score?: number | null;
  rating_mu?: number | null;
  rating_sigma?: number | null;
  rating?: number | null;
  elo?: number | null;
  active: boolean;
};

export type GameParticipant = {
  bot_id: string;
  agent_id: string;
  seat: number;
  placement: number;
  ending_chips: number;
  eliminated_round?: number | null;
};

export type Game = {
  game_id: string;
  status: string;
  started_at?: string | number;
  finished_at?: string | number;
  duration_seconds?: number | null;
  table_size?: number;
  round_count?: number;
  max_rounds_reached?: boolean;
  participants?: GameParticipant[];
  bot_a_id?: string | null;
  bot_b_id?: string | null;
  agent_a_id?: string | null;
  agent_b_id?: string | null;
  winner_bot_id?: string | null;
  loser_bot_id?: string | null;
  reason?: string;
};

export type Offer = {
  offer_id: string;
  seller_agent_id: string;
  bot_id?: string;
  title: string;
  description: string;
  evidence?: string;
  file_paths?: string[];
  price_pct: number;
  review_count?: number;
  status?: string;
  created_at?: string | number;
  updated_at?: string | number;
};

export type Purchase = {
  purchase_id: string;
  offer_id: string;
  buyer_agent_id: string;
  seller_agent_id?: string;
  price_pct?: number;
  file_paths?: string[];
  created_at?: string | number;
};

export type Review = {
  review_id: string;
  offer_id: string;
  buyer_agent_id: string;
  text: string;
  created_at?: string;
};

export type LeaderboardAgent = {
  agentId: string;
  bestBotId: string | null;
  bestRating: number;
  status: string;
};

export type LeaderboardBot = {
  botId: string;
  agentId: string;
  name: string;
  rating: number;
  active: boolean;
};

export type SnapshotPayload = {
  leaderboard: {
    agents: LeaderboardAgent[];
    bots: LeaderboardBot[];
  };
  counts: {
    agentCount: number;
    botCount: number;
    activeBotCount: number;
    gameCount: number;
    offerCount: number;
    purchaseCount: number;
    reviewCount: number;
  };
};

export type Snapshot = {
  createdAt: number;
  status: string;
  bestAgentId: string | null;
  bestRating: number | null;
  botCount: number;
  activeBotCount: number;
  gameCount: number;
  offerCount: number;
  payload: SnapshotPayload;
};

export type RunEvent = {
  eventId: string;
  kind: string;
  createdAt: number;
  payload: Record<string, unknown>;
};

export type AgentConversationBlock = {
  block_id: string;
  run_id: string;
  agent_id: string;
  step_id?: string | null;
  role: "user" | "assistant" | "tool" | "system";
  kind: "prompt" | "text" | "tool" | "warning" | "summary" | "error";
  title: string;
  text: string;
  collapsed: boolean;
  streaming: boolean;
  created_at: string | number;
  updated_at: string | number;
};

export type RunComment = {
  commentId: string;
  createdAt: number;
  author: string;
  commentatorId?: string | null;
  parentMessageId?: string | null;
  source: string;
  body: string;
  offerId: string | null;
};

export type ChatMessage = {
  messageId: string;
  createdAt: number;
  author: string;
  channel: string;
  body: string;
  kind: string;
};

export type RunDetail = {
  runId: string;
  name: string;
  description: string;
  status: string;
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  updatedAt: number;
  agentCount: number;
  botCount: number;
  activeBotCount: number;
  gameCount: number;
  offerCount: number;
  purchaseCount: number;
  reviewCount: number;
  bestAgentId: string | null;
  bestRating: number | null;
  config: RunConfig;
  state: RawRunState;
  leaderboard: {
    agents: LeaderboardAgent[];
    bots: LeaderboardBot[];
  };
  finalScores?: Record<string, number> | null;
  payouts?: Record<string, number> | null;
};

export type RunStateCollection<T> = Record<string, T> | T[];

export type RawRunState = {
  agents: RunStateCollection<AgentState>;
  bots: RunStateCollection<BotState>;
  games: RunStateCollection<Game>;
  offers: RunStateCollection<Offer>;
  purchases: RunStateCollection<Purchase>;
  reviews: RunStateCollection<Review>;
  [key: string]: unknown;
};

export type RunState = {
  agents: Record<string, AgentState>;
  bots: Record<string, BotState>;
  games: Record<string, Game>;
  offers: Record<string, Offer>;
  purchases: Record<string, Purchase>;
  reviews: Record<string, Review>;
  [key: string]: unknown;
};

export type RunDashboard = {
  run: RunDetail;
  snapshots: Snapshot[];
  events: RunEvent[];
  comments: RunComment[];
  feedMessages: RunComment[];
  chatMessages: ChatMessage[];
};

export type RunMarketplaceAnalysisSummary = {
  totalOffers: number;
  totalPurchases: number;
  totalReviews: number;
  avgPricePct: number;
  mostActiveSeller: string | null;
  mostActiveBuyer: string | null;
  sameModelPurchasePct: number | null;
};

export type RunAnalysisSummary = {
  runId: string;
  status: string;
  updatedAt: number;
  giniCoefficient: number;
  avgAggression: number;
  botsSubmitted: number;
  chatMessageCount: number;
  topAgentElo: number | null;
  marketplace: RunMarketplaceAnalysisSummary;
};

export type CompareResourceStatus = "idle" | "loading" | "success" | "error";

export type CompareResourceState<T> = {
  status: CompareResourceStatus;
  data?: T;
  error?: string | null;
  sourceUpdatedAt?: number;
};

export type ComparePreset = {
  id: string;
  name: string;
  runIds: string[];
  createdAt: number;
  updatedAt: number;
};

export type RunListItem = {
  runId: string;
  name: string;
  description: string;
  status: string;
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  updatedAt: number;
  agentCount: number;
  botCount: number;
  gameCount: number;
  offerCount: number;
  bestAgentId: string | null;
  bestRating: number | null;
};

export type CommentContentFilter = "all" | "strategic" | "social";

export type TabId = "tournament" | "marketplace" | "agents" | "comments" | "events" | "compare";
