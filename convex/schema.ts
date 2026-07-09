import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  goaRuns: defineTable({
    runId: v.string(),
    name: v.string(),
    description: v.string(),
    status: v.string(),
    createdAt: v.number(),
    startedAt: v.optional(v.number()),
    finishedAt: v.optional(v.number()),
    updatedAt: v.number(),
    agentCount: v.number(),
    botCount: v.number(),
    activeBotCount: v.number(),
    gameCount: v.number(),
    offerCount: v.number(),
    purchaseCount: v.number(),
    reviewCount: v.number(),
    bestAgentId: v.optional(v.string()),
    bestRating: v.optional(v.number()),
    bestElo: v.optional(v.number()),
    stateJson: v.optional(v.string()),
    leaderboardJson: v.optional(v.string()),
    configJson: v.string(),
    finalScoresJson: v.string(),
    payoutsJson: v.string(),
  })
    .index("by_run_id", ["runId"])
    .index("by_updated_at", ["updatedAt"]),
  goaRunAgents: defineTable({
    runId: v.string(),
    agentId: v.string(),
    status: v.string(),
    bestRating: v.number(),
    updatedAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_agent_id", ["runId", "agentId"])
    .index("by_run_id_best_rating", ["runId", "bestRating"]),
  goaRunBots: defineTable({
    runId: v.string(),
    botId: v.string(),
    agentId: v.string(),
    active: v.boolean(),
    rating: v.number(),
    name: v.string(),
    updatedAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_bot_id", ["runId", "botId"])
    .index("by_run_id_rating", ["runId", "rating"]),
  goaRunGames: defineTable({
    runId: v.string(),
    gameId: v.string(),
    status: v.string(),
    startedAt: v.number(),
    finishedAt: v.optional(v.number()),
    winnerBotId: v.optional(v.string()),
    tableSize: v.number(),
    roundCount: v.number(),
    durationSeconds: v.optional(v.number()),
    updatedAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_game_id", ["runId", "gameId"])
    .index("by_run_id_started_at", ["runId", "startedAt"]),
  goaRunOffers: defineTable({
    runId: v.string(),
    offerId: v.string(),
    agentId: v.string(),
    botId: v.string(),
    pricePct: v.number(),
    title: v.string(),
    updatedAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_offer_id", ["runId", "offerId"])
    .index("by_run_id_updated_at", ["runId", "updatedAt"]),
  goaRunPurchases: defineTable({
    runId: v.string(),
    purchaseId: v.string(),
    offerId: v.string(),
    buyerAgentId: v.string(),
    updatedAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_purchase_id", ["runId", "purchaseId"])
    .index("by_run_id_updated_at", ["runId", "updatedAt"])
    .index("by_run_id_offer_id", ["runId", "offerId"])
    .index("by_run_id_buyer_agent_id", ["runId", "buyerAgentId"]),
  goaRunReviews: defineTable({
    runId: v.string(),
    reviewId: v.string(),
    offerId: v.string(),
    buyerAgentId: v.string(),
    createdAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_review_id", ["runId", "reviewId"])
    .index("by_run_id_created_at", ["runId", "createdAt"]),
  goaRunComments: defineTable({
    runId: v.string(),
    commentId: v.string(),
    authorAgentId: v.string(),
    createdAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_comment_id", ["runId", "commentId"])
    .index("by_run_id_created_at", ["runId", "createdAt"]),
  goaRunCommentReactions: defineTable({
    runId: v.string(),
    commentId: v.string(),
    emoji: v.string(),
    authorAgentId: v.string(),
    createdAt: v.number(),
  })
    .index("by_run_id_comment_id", ["runId", "commentId"])
    .index("by_run_id_comment_id_emoji_author_agent_id", ["runId", "commentId", "emoji", "authorAgentId"]),
  goaRunSnapshots: defineTable({
    runId: v.string(),
    createdAt: v.number(),
    status: v.string(),
    bestAgentId: v.optional(v.string()),
    bestRating: v.optional(v.number()),
    bestElo: v.optional(v.number()),
    botCount: v.number(),
    activeBotCount: v.number(),
    gameCount: v.number(),
    offerCount: v.number(),
    payloadJson: v.string(),
  }).index("by_run_id_created_at", ["runId", "createdAt"]),
  goaAgentProfiles: defineTable({
    name: v.string(),
    model: v.string(),
    internetAccess: v.boolean(),
    personality: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
  }).index("by_name", ["name"]),
  goaRunEvents: defineTable({
    runId: v.string(),
    kind: v.string(),
    createdAt: v.number(),
    payloadJson: v.string(),
  })
    .index("by_run_id_created_at", ["runId", "createdAt"])
    .index("by_run_id_kind_created_at", ["runId", "kind", "createdAt"]),
  goaRunControls: defineTable({
    runId: v.string(),
    status: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
    startedAt: v.optional(v.number()),
    stopRequestedAt: v.optional(v.number()),
    stoppedAt: v.optional(v.number()),
    orchestratorId: v.optional(v.string()),
    stopReason: v.optional(v.string()),
    configJson: v.string(),
    metadataJson: v.string(),
  })
    .index("by_run_id", ["runId"])
    .index("by_status_updated_at", ["status", "updatedAt"]),
  goaRunSandboxes: defineTable({
    runId: v.string(),
    sandboxId: v.string(),
    agentId: v.string(),
    kind: v.string(),
    status: v.string(),
    registeredAt: v.number(),
    lastHeartbeatAt: v.number(),
    heartbeatTtlSeconds: v.optional(v.number()),
    metadataJson: v.string(),
  })
    .index("by_run_id_sandbox_id", ["runId", "sandboxId"])
    .index("by_run_id_agent_id", ["runId", "agentId"])
    .index("by_run_id_last_heartbeat_at", ["runId", "lastHeartbeatAt"]),
  goaRunBotSubmissionQueue: defineTable({
    runId: v.string(),
    submissionId: v.string(),
    agentId: v.string(),
    sandboxId: v.optional(v.string()),
    botId: v.optional(v.string()),
    status: v.string(),
    priority: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
    claimedAt: v.optional(v.number()),
    claimedBySandboxId: v.optional(v.string()),
    leaseExpiresAt: v.optional(v.number()),
    completedAt: v.optional(v.number()),
    error: v.optional(v.string()),
    payloadJson: v.string(),
    resultJson: v.optional(v.string()),
  })
    .index("by_run_id_submission_id", ["runId", "submissionId"])
    .index("by_run_id_created_at", ["runId", "createdAt"])
    .index("by_run_id_status_created_at", ["runId", "status", "createdAt"])
    .index("by_run_id_agent_id_created_at", ["runId", "agentId", "createdAt"]),
  goaRunRuntimeLogs: defineTable({
    runId: v.string(),
    logId: v.string(),
    createdAt: v.number(),
    agentId: v.optional(v.string()),
    sandboxId: v.optional(v.string()),
    sessionId: v.optional(v.string()),
    blockId: v.optional(v.string()),
    parentBlockId: v.optional(v.string()),
    replyToLogId: v.optional(v.string()),
    kind: v.string(),
    role: v.string(),
    channel: v.string(),
    sequence: v.number(),
    title: v.optional(v.string()),
    text: v.string(),
    payloadJson: v.string(),
  })
    .index("by_run_id_log_id", ["runId", "logId"])
    .index("by_run_id_created_at", ["runId", "createdAt"])
    .index("by_run_id_agent_id_created_at", ["runId", "agentId", "createdAt"])
    .index("by_run_id_sandbox_id_created_at", ["runId", "sandboxId", "createdAt"])
    .index("by_run_id_block_id_created_at", ["runId", "blockId", "createdAt"]),
  goaRunAgentSteers: defineTable({
    runId: v.string(),
    steerId: v.string(),
    agentId: v.string(),
    status: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
    appliedAt: v.optional(v.number()),
    text: v.string(),
    payloadJson: v.string(),
  })
    .index("by_run_id_steer_id", ["runId", "steerId"])
    .index("by_run_id_agent_id_created_at", ["runId", "agentId", "createdAt"])
    .index("by_run_id_agent_id_status_created_at", ["runId", "agentId", "status", "createdAt"]),
});
