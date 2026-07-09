import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

type GenericRecord = Record<string, unknown>;

type ParsedEvent = {
  eventId: string;
  kind: string;
  createdAt: number;
  payload: GenericRecord;
};

type RunChunkKind =
  | "agents"
  | "bots"
  | "games"
  | "offers"
  | "purchases"
  | "reviews"
  | "comments";

const RECENT_GAME_FEED_LIMIT = 16;

function parseTimestamp(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function asRecord(value: unknown): GenericRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as GenericRecord) : {};
}

function asRecordArray(value: unknown): GenericRecord[] {
  if (Array.isArray(value)) {
    return value.map((item) => asRecord(item));
  }
  return Object.values(asRecord(value)).map((item) => asRecord(item));
}

function stringify(value: unknown): string {
  return JSON.stringify(value ?? null);
}

function parseJson<T>(value: string): T {
  return JSON.parse(value) as T;
}

function hydrateGamePayload(row: any) {
  const payload = parseJson<GenericRecord>(row.payloadJson);
  return {
    game_id: String((payload.game_id as string | undefined) ?? row.gameId),
    run_id: String((payload.run_id as string | undefined) ?? row.runId),
    status: String((payload.status as string | undefined) ?? row.status ?? "finished"),
    table_size: Number((payload.table_size as number | undefined) ?? row.tableSize ?? 0),
    round_count: Number((payload.round_count as number | undefined) ?? row.roundCount ?? 0),
    max_rounds_reached: Boolean(payload.max_rounds_reached),
    participants: Array.isArray(payload.participants) ? payload.participants : [],
    winner_bot_id:
      (typeof payload.winner_bot_id === "string" && payload.winner_bot_id.length > 0
        ? payload.winner_bot_id
        : typeof row.winnerBotId === "string" && row.winnerBotId.length > 0
          ? row.winnerBotId
          : null),
    loser_bot_id: typeof payload.loser_bot_id === "string" ? payload.loser_bot_id : null,
    reason: typeof payload.reason === "string" ? payload.reason : null,
    actions: [],
    started_at: payload.started_at ?? new Date(row.startedAt).toISOString(),
    finished_at:
      row.finishedAt === undefined || row.finishedAt === null
        ? payload.finished_at ?? null
        : payload.finished_at ?? new Date(row.finishedAt).toISOString(),
    duration_seconds:
      row.durationSeconds === undefined || row.durationSeconds === null
        ? payload.duration_seconds ?? null
        : row.durationSeconds,
    bot_a_id: typeof payload.bot_a_id === "string" ? payload.bot_a_id : null,
    bot_b_id: typeof payload.bot_b_id === "string" ? payload.bot_b_id : null,
    agent_a_id: typeof payload.agent_a_id === "string" ? payload.agent_a_id : null,
    agent_b_id: typeof payload.agent_b_id === "string" ? payload.agent_b_id : null,
  };
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function effectiveRunStatus(status: string, finishedAt?: number | null): string {
  if (status === "failed") {
    return "failed";
  }
  if (finishedAt !== null && finishedAt !== undefined) {
    return "finished";
  }
  return status;
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sandboxHeartbeatTtlSeconds(kind: string, config: GenericRecord, explicitTtl?: number | null): number {
  if (typeof explicitTtl === "number" && Number.isFinite(explicitTtl) && explicitTtl > 0) {
    return explicitTtl;
  }
  const pollSeconds =
    kind === "tournament"
      ? Number(config.tournament_poll_seconds ?? 0.25)
      : Number(config.agent_poll_seconds ?? 2);
  const base = kind === "tournament" ? 30 : 120;
  return Math.max(base, Math.ceil(Math.max(1, pollSeconds) * 10));
}

function effectiveSandboxStatus(
  sandbox: { kind?: string; status?: string; lastHeartbeatAt?: number | null; heartbeatTtlSeconds?: number | null },
  config: GenericRecord,
  now: number,
): string {
  const status = String(sandbox.status ?? "running");
  if (status === "finished" || status === "failed") {
    return status;
  }
  const lastHeartbeatAt = numberOrNull(sandbox.lastHeartbeatAt);
  if (lastHeartbeatAt === null) {
    return status;
  }
  const ttlSeconds = sandboxHeartbeatTtlSeconds(String(sandbox.kind ?? "agent"), config, sandbox.heartbeatTtlSeconds ?? null);
  if (lastHeartbeatAt + ttlSeconds * 1000 < now) {
    return "stale";
  }
  return status;
}

function plannedRunFinishAt(
  run: { startedAt?: number | null; configJson?: string },
  config: GenericRecord,
): number | null {
  const startedAt = numberOrNull(run.startedAt);
  if (startedAt === null) {
    return null;
  }
  const durationMinutes = Number(config.duration_minutes ?? 0);
  const convergenceTailMinutes = Number(config.convergence_tail_minutes ?? 0);
  return startedAt + (durationMinutes + convergenceTailMinutes) * 60 * 1000;
}

function deriveRunLifecycle(
  run: {
    status?: string;
    startedAt?: number | null;
    finishedAt?: number | null;
    updatedAt?: number | null;
    configJson?: string;
  },
  control: { status?: string } | null,
  sandboxes: Array<{ kind?: string; status?: string; lastHeartbeatAt?: number | null; heartbeatTtlSeconds?: number | null }>,
  now: number,
): { status: string; finishedAt: number | null } {
  const config = parseJson<GenericRecord>(run.configJson ?? "{}");
  const persistedStatus = String(run.status ?? "pending");
  const controlStatus = control ? String(control.status ?? persistedStatus) : persistedStatus;
  const persistedFinishedAt = numberOrNull(run.finishedAt);
  if (persistedStatus === "failed" || controlStatus === "failed") {
    return { status: "failed", finishedAt: persistedFinishedAt };
  }
  if (persistedFinishedAt !== null || controlStatus === "finished" || persistedStatus === "finished") {
    return { status: "finished", finishedAt: persistedFinishedAt };
  }
  if (controlStatus === "stopping" || persistedStatus === "stopping") {
    return { status: "stopping", finishedAt: persistedFinishedAt };
  }

  const deadline = plannedRunFinishAt(run, config);
  const latestHeartbeatAt = sandboxes.reduce((max, sandbox) => {
    const heartbeat = numberOrNull(sandbox.lastHeartbeatAt) ?? 0;
    return Math.max(max, heartbeat);
  }, 0);
  const derivedFinishedAt = Math.max(
    deadline ?? 0,
    latestHeartbeatAt,
    numberOrNull(run.updatedAt) ?? 0,
  );
  const tournament = sandboxes
    .filter((sandbox) => String(sandbox.kind ?? "") === "tournament")
    .sort((left, right) => (numberOrNull(right.lastHeartbeatAt) ?? 0) - (numberOrNull(left.lastHeartbeatAt) ?? 0))[0];
  const tournamentStatus = tournament ? effectiveSandboxStatus(tournament, config, now) : null;
  if (deadline !== null && now >= deadline && tournamentStatus !== "running" && tournamentStatus !== "stopping") {
    return {
      status: "finished",
      finishedAt: derivedFinishedAt > 0 ? Math.min(now, derivedFinishedAt) : deadline,
    };
  }
  const activeSandboxes = sandboxes.filter((sandbox) => {
    const status = effectiveSandboxStatus(sandbox, config, now);
    return status === "running" || status === "stopping";
  });
  if (tournamentStatus === "stale" && activeSandboxes.length === 0) {
    return { status: "failed", finishedAt: null };
  }
  return { status: persistedStatus, finishedAt: persistedFinishedAt };
}

function requireSyncToken(syncToken: string | undefined) {
  const expected = process.env.CONVEX_SYNC_TOKEN;
  if (!expected) {
    return;
  }
  if (syncToken !== expected) {
    throw new Error("unauthorized");
  }
}

function commentBodyForEvent(kind: string, payload: GenericRecord): string | null {
  if (kind === "agent.failed") {
    return typeof payload.error === "string" ? payload.error.trim() : null;
  }
  if (kind === "run.finished") {
    const finalScores = asRecord(payload.final_scores);
    const agents = Object.keys(finalScores);
    if (agents.length === 0) {
      return "Run finished.";
    }
    const summary = agents
      .slice(0, 4)
      .map((agentId) => `${agentId}: ${Number(finalScores[agentId] ?? 0).toFixed(1)}`)
      .join(", ");
    return `Run finished. Final ratings: ${summary}`;
  }
  if (kind === "run.failed") {
    return typeof payload.error === "string" ? payload.error.trim() : "Run failed.";
  }
  if (typeof payload.text === "string" && payload.text.trim()) {
    return payload.text.trim();
  }
  if (typeof payload.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }
  return null;
}

function commentSourceForEvent(kind: string): string | null {
  switch (kind) {
    case "offer.reviewed":
      return "Marketplace Review";
    case "agent.step":
      return "Agent Note";
    case "agent.message":
      return "Controller Message";
    case "agent.warning":
      return "Time Warning";
    case "agent.failed":
      return "Agent Failure";
    case "run.finished":
      return "Run Result";
    case "run.failed":
      return "Run Failure";
    default:
      return null;
  }
}

function buildComments(
  commentsState: unknown,
  reviewsState: unknown,
  events: ParsedEvent[],
) {
  const comments: Array<{
    commentId: string;
    createdAt: number;
    author: string;
    source: string;
    body: string;
    offerId: string | null;
  }> = [];
  const seen = new Set<string>();

  for (const message of asRecordArray(commentsState)) {
    const body = typeof message.text === "string" ? message.text.trim() : "";
    if (!body) {
      continue;
    }
    const commentId = String(message.message_id ?? `comment-${comments.length}`);
    seen.add(commentId);
    comments.push({
      commentId,
      createdAt: parseTimestamp(message.created_at, Date.now()),
      author: stringOrNull(message.author_agent_id) ?? "unknown",
      source: "Comment Feed",
      body,
      offerId: null,
    });
  }

  for (const review of asRecordArray(reviewsState)) {
    const body = typeof review.text === "string" ? review.text.trim() : "";
    if (!body) {
      continue;
    }
    const commentId = String(review.review_id ?? `review-${comments.length}`);
    seen.add(commentId);
    comments.push({
      commentId,
      createdAt: parseTimestamp(review.created_at, Date.now()),
      author: stringOrNull(review.buyer_agent_id) ?? "unknown",
      source: "Marketplace Review",
      body,
      offerId: stringOrNull(review.offer_id),
    });
  }

  for (const event of events) {
    const source = commentSourceForEvent(event.kind);
    const body = source ? commentBodyForEvent(event.kind, event.payload) : null;
    if (!source || !body) {
      continue;
    }
    const commentId = `${event.kind}:${event.eventId}`;
    if (seen.has(commentId)) {
      continue;
    }
    comments.push({
      commentId,
      createdAt: event.createdAt,
      author:
        stringOrNull(event.payload.agent_id) ??
        stringOrNull(event.payload.buyer_agent_id) ??
        "system",
      source,
      body,
      offerId: stringOrNull(event.payload.offer_id),
    });
  }

  return comments.sort((left, right) => right.createdAt - left.createdAt).slice(0, 40);
}

function buildFeedMessages(commentsState: unknown) {
  return asRecordArray(commentsState)
    .map((message) => ({
      commentId: String(message.message_id ?? ""),
      createdAt: parseTimestamp(message.created_at, Date.now()),
      author: stringOrNull(message.author_agent_id) ?? "unknown",
      commentatorId: stringOrNull(message.commentator_id),
      parentMessageId: stringOrNull(message.parent_message_id),
      source: "Comment Feed",
      body: String(message.text ?? "").trim(),
      offerId: null,
    }))
    .filter((message) => message.commentId && message.body)
    .sort((left, right) => left.createdAt - right.createdAt)
    .slice(-200);
}

function pushChatMessage(
  messages: Array<{
    messageId: string;
    createdAt: number;
    author: string;
    channel: string;
    body: string;
    kind: string;
  }>,
  event: ParsedEvent,
  channel: string,
  body: string,
) {
  const normalized = body.trim();
  if (!normalized) {
    return;
  }
  const lines = normalized
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 12);
  lines.forEach((line, index) => {
    messages.push({
      messageId: `${event.eventId}:${channel}:${index}`,
      createdAt: event.createdAt + index,
      author: stringOrNull(event.payload.agent_id) ?? "system",
      channel,
      body: line,
      kind: event.kind,
    });
  });
}

function buildChatMessages(events: ParsedEvent[]) {
  const messages: Array<{
    messageId: string;
    createdAt: number;
    author: string;
    channel: string;
    body: string;
    kind: string;
  }> = [];

  for (const event of events) {
    if (event.kind === "agent.output.chunk") {
      pushChatMessage(
        messages,
        event,
        stringOrNull(event.payload.stream) ?? "output",
        String(event.payload.chunk ?? ""),
      );
      continue;
    }
    if (event.kind === "agent.output") {
      pushChatMessage(messages, event, "summary", String(event.payload.output ?? ""));
      continue;
    }
    if (event.kind === "agent.step" || event.kind === "agent.message" || event.kind === "agent.warning") {
      pushChatMessage(messages, event, "update", String(event.payload.message ?? ""));
      continue;
    }
    if (event.kind === "agent.failed") {
      pushChatMessage(messages, event, "error", String(event.payload.error ?? ""));
    }
  }

  return messages.sort((left, right) => left.createdAt - right.createdAt).slice(-120);
}

function chunkTableInfo(kind: RunChunkKind) {
  switch (kind) {
    case "agents":
      return { table: "goaRunAgents" as const, idField: "agentId", payloadField: "agent_id" };
    case "bots":
      return { table: "goaRunBots" as const, idField: "botId", payloadField: "bot_id" };
    case "games":
      return { table: "goaRunGames" as const, idField: "gameId", payloadField: "game_id" };
    case "offers":
      return { table: "goaRunOffers" as const, idField: "offerId", payloadField: "offer_id" };
    case "purchases":
      return { table: "goaRunPurchases" as const, idField: "purchaseId", payloadField: "purchase_id" };
    case "reviews":
      return { table: "goaRunReviews" as const, idField: "reviewId", payloadField: "review_id" };
    case "comments":
      return { table: "goaRunComments" as const, idField: "commentId", payloadField: "message_id" };
  }
}

async function findChunkRow(ctx: any, kind: RunChunkKind, runId: string, itemId: string) {
  switch (kind) {
    case "agents":
      return ctx.db
        .query("goaRunAgents")
        .withIndex("by_run_id_agent_id", (q: any) => q.eq("runId", runId).eq("agentId", itemId))
        .unique();
    case "bots":
      return ctx.db
        .query("goaRunBots")
        .withIndex("by_run_id_bot_id", (q: any) => q.eq("runId", runId).eq("botId", itemId))
        .unique();
    case "games":
      return ctx.db
        .query("goaRunGames")
        .withIndex("by_run_id_game_id", (q: any) => q.eq("runId", runId).eq("gameId", itemId))
        .unique();
    case "offers":
      return ctx.db
        .query("goaRunOffers")
        .withIndex("by_run_id_offer_id", (q: any) => q.eq("runId", runId).eq("offerId", itemId))
        .unique();
    case "purchases":
      return ctx.db
        .query("goaRunPurchases")
        .withIndex("by_run_id_purchase_id", (q: any) => q.eq("runId", runId).eq("purchaseId", itemId))
        .unique();
    case "reviews":
      return ctx.db
        .query("goaRunReviews")
        .withIndex("by_run_id_review_id", (q: any) => q.eq("runId", runId).eq("reviewId", itemId))
        .unique();
    case "comments":
      return ctx.db
        .query("goaRunComments")
        .withIndex("by_run_id_comment_id", (q: any) => q.eq("runId", runId).eq("commentId", itemId))
        .unique();
  }
}

function normalizeChunkDocument(kind: RunChunkKind, runId: string, item: GenericRecord, now: number) {
  const payloadJson = stringify(item);
  switch (kind) {
    case "agents":
      return {
        runId,
        agentId: String(item.agent_id ?? ""),
        status: String(item.status ?? "unknown"),
        bestRating: Number(item.best_rating_score ?? item.best_elo ?? 0),
        updatedAt: now,
        payloadJson,
      };
    case "bots":
      return {
        runId,
        botId: String(item.bot_id ?? ""),
        agentId: String(item.agent_id ?? ""),
        active: Boolean(item.active),
        rating: Number(item.rating_score ?? item.elo ?? 0),
        name: String(item.name ?? ""),
        updatedAt: now,
        payloadJson,
      };
    case "games":
      return {
        runId,
        gameId: String(item.game_id ?? ""),
        status: String(item.status ?? "unknown"),
        startedAt: parseTimestamp(item.started_at, now),
        finishedAt:
          item.finished_at === null || item.finished_at === undefined
            ? undefined
            : parseTimestamp(item.finished_at, now),
        winnerBotId: stringOrNull(item.winner_bot_id) ?? undefined,
        tableSize: Number(item.table_size ?? 0),
        roundCount: Number(item.round_count ?? 0),
        durationSeconds:
          item.duration_seconds === null || item.duration_seconds === undefined
            ? undefined
            : Number(item.duration_seconds),
        updatedAt: now,
        payloadJson,
      };
    case "offers":
      return {
        runId,
        offerId: String(item.offer_id ?? ""),
        agentId: String(item.agent_id ?? ""),
        botId: String(item.bot_id ?? ""),
        pricePct: Number(item.price_pct ?? 0),
        title: String(item.title ?? ""),
        updatedAt: now,
        payloadJson,
      };
    case "purchases":
      return {
        runId,
        purchaseId: String(item.purchase_id ?? ""),
        offerId: String(item.offer_id ?? ""),
        buyerAgentId: String(item.buyer_agent_id ?? ""),
        updatedAt: now,
        payloadJson,
      };
    case "reviews":
      return {
        runId,
        reviewId: String(item.review_id ?? ""),
        offerId: String(item.offer_id ?? ""),
        buyerAgentId: String(item.buyer_agent_id ?? ""),
        createdAt: parseTimestamp(item.created_at, now),
        payloadJson,
      };
    case "comments":
      return {
        runId,
        commentId: String(item.message_id ?? ""),
        authorAgentId: String(item.author_agent_id ?? ""),
        createdAt: parseTimestamp(item.created_at, now),
        payloadJson,
      };
  }
}

async function collectRunState(ctx: any, runId: string) {
  const [agents, bots, games, offers, purchases, reviews, comments] = await Promise.all([
    ctx.db.query("goaRunAgents").withIndex("by_run_id_best_rating", (q) => q.eq("runId", runId)).order("desc").collect(),
    ctx.db.query("goaRunBots").withIndex("by_run_id_rating", (q) => q.eq("runId", runId)).order("desc").collect(),
    ctx.db
      .query("goaRunGames")
      .withIndex("by_run_id_started_at", (q) => q.eq("runId", runId))
      .order("desc")
      .take(RECENT_GAME_FEED_LIMIT),
    ctx.db.query("goaRunOffers").withIndex("by_run_id_offer_id", (q) => q.eq("runId", runId)).take(1000),
    ctx.db.query("goaRunPurchases").withIndex("by_run_id_purchase_id", (q) => q.eq("runId", runId)).take(5000),
    ctx.db.query("goaRunReviews").withIndex("by_run_id_review_id", (q) => q.eq("runId", runId)).take(1000),
    ctx.db.query("goaRunComments").withIndex("by_run_id_comment_id", (q) => q.eq("runId", runId)).take(2000),
  ]);

  const agentsState = Object.fromEntries(
    agents.map((row) => [row.agentId, parseJson<GenericRecord>(row.payloadJson)]),
  );
  const botsState = Object.fromEntries(
    bots.map((row) => [row.botId, parseJson<GenericRecord>(row.payloadJson)]),
  );
  const gamesState = Object.fromEntries(
    games.map((row) => [row.gameId, hydrateGamePayload(row)]),
  );
  const offersState = Object.fromEntries(
    offers.map((row) => [row.offerId, parseJson<GenericRecord>(row.payloadJson)]),
  );
  const purchasesState = Object.fromEntries(
    purchases.map((row) => [row.purchaseId, parseJson<GenericRecord>(row.payloadJson)]),
  );
  const reviewsState = Object.fromEntries(
    reviews.map((row) => [row.reviewId, parseJson<GenericRecord>(row.payloadJson)]),
  );
  const commentsState = Object.fromEntries(
    comments.map((row) => [row.commentId, parseJson<GenericRecord>(row.payloadJson)]),
  );

  return {
    state: {
      agents: Object.values(agentsState),
      bots: Object.values(botsState),
      games: Object.values(gamesState),
      offers: Object.values(offersState),
      purchases: Object.values(purchasesState),
      reviews: Object.values(reviewsState),
      comments: Object.values(commentsState),
    },
    leaderboard: {
      agents: agents.map((row) => ({
        agentId: row.agentId,
        bestBotId: stringOrNull(parseJson<GenericRecord>(row.payloadJson).best_bot_id),
        bestRating: row.bestRating,
        status: row.status,
      })),
      bots: bots.map((row) => ({
        botId: row.botId,
        agentId: row.agentId,
        rating: row.rating,
        active: row.active,
        name: row.name,
      })),
    },
  };
}

function overlayAgentSandboxState(
  agents: GenericRecord[],
  sandboxes: any[],
  config: GenericRecord,
  now: number,
) {
  const latestByAgent = new Map<string, any>();
  for (const row of sandboxes) {
    if (row.kind !== "agent" || !row.agentId) {
      continue;
    }
    const current = latestByAgent.get(String(row.agentId));
    const currentTs = current ? Number(current.lastHeartbeatAt ?? current.registeredAt ?? 0) : -1;
    const rowTs = Number(row.lastHeartbeatAt ?? row.registeredAt ?? 0);
    if (!current || rowTs >= currentTs) {
      latestByAgent.set(String(row.agentId), row);
    }
  }
  for (const payload of agents) {
    const agentId = String(payload.agent_id ?? "");
    const sandbox = latestByAgent.get(agentId);
    if (!sandbox) {
      continue;
    }
    const metadata = parseJson<GenericRecord>(sandbox.metadataJson) ?? {};
    payload.sandbox_id = sandbox.sandboxId;
    payload.sandbox_status = effectiveSandboxStatus(sandbox, config, now);
    payload.last_activity_at = new Date(Number(sandbox.lastHeartbeatAt ?? sandbox.registeredAt ?? now)).toISOString();
    if (metadata.diagnostics && typeof metadata.diagnostics === "object" && !Array.isArray(metadata.diagnostics)) {
      payload.diagnostics = metadata.diagnostics;
    }
  }
}

export const syncRunSummary = mutation({
  args: { summary: v.any(), syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const summary = asRecord(args.summary);
    const runId = String(summary.runId ?? "");
    const existing = await ctx.db
      .query("goaRuns")
      .withIndex("by_run_id", (q) => q.eq("runId", runId))
      .unique();

    const document = {
      runId,
      name: String(summary.name ?? ""),
      description: String(summary.description ?? ""),
      status: String(summary.status ?? "unknown"),
      createdAt: parseTimestamp(summary.createdAt, Date.now()),
      startedAt:
        summary.startedAt === null || summary.startedAt === undefined
          ? undefined
          : parseTimestamp(summary.startedAt, Date.now()),
      finishedAt:
        summary.finishedAt === null || summary.finishedAt === undefined
          ? undefined
          : parseTimestamp(summary.finishedAt, Date.now()),
      updatedAt: parseTimestamp(summary.updatedAt, Date.now()),
      agentCount: Number(summary.agentCount ?? 0),
      botCount: Number(summary.botCount ?? 0),
      activeBotCount: Number(summary.activeBotCount ?? 0),
      gameCount: Number(summary.gameCount ?? 0),
      offerCount: Number(summary.offerCount ?? 0),
      purchaseCount: Number(summary.purchaseCount ?? 0),
      reviewCount: Number(summary.reviewCount ?? 0),
      bestAgentId: stringOrNull(summary.bestAgentId) ?? undefined,
      bestRating:
        summary.bestRating === null || summary.bestRating === undefined
          ? undefined
          : Number(summary.bestRating),
      bestElo:
        summary.bestElo === null || summary.bestElo === undefined
          ? undefined
          : Number(summary.bestElo),
      configJson: stringify(summary.config ?? {}),
      finalScoresJson: stringify(summary.finalScores ?? {}),
      payoutsJson: stringify(summary.payouts ?? {}),
    };

    if (existing) {
      await ctx.db.patch(existing._id, document);
    } else {
      await ctx.db.insert("goaRuns", document);
    }

    await ctx.db.insert("goaRunSnapshots", {
      runId,
      createdAt: document.updatedAt,
      status: document.status,
      bestAgentId: document.bestAgentId,
      bestRating: document.bestRating,
      bestElo: document.bestElo,
      botCount: document.botCount,
      activeBotCount: document.activeBotCount,
      gameCount: document.gameCount,
      offerCount: document.offerCount,
      payloadJson: stringify(summary.snapshotPayload ?? {}),
    });
  },
});

export const syncRunChunk = mutation({
  args: {
    runId: v.string(),
    kind: v.union(
      v.literal("agents"),
      v.literal("bots"),
      v.literal("games"),
      v.literal("offers"),
      v.literal("purchases"),
      v.literal("reviews"),
      v.literal("comments"),
    ),
    items: v.array(v.any()),
    removedIds: v.optional(v.array(v.string())),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const info = chunkTableInfo(args.kind);
    const now = Date.now();

    for (const rawItem of args.items) {
      const item = asRecord(rawItem);
      const itemId = String(item[info.payloadField] ?? "");
      if (!itemId) {
        continue;
      }
      const document = normalizeChunkDocument(args.kind, args.runId, item, now);
      const existing = await findChunkRow(ctx, args.kind, args.runId, itemId);
      if (existing) {
        await ctx.db.patch(existing._id, document);
      } else {
        await ctx.db.insert(info.table, document as never);
      }
    }

    for (const removedId of args.removedIds ?? []) {
      const existing = await findChunkRow(ctx, args.kind, args.runId, removedId);
      if (existing) {
        await ctx.db.delete(existing._id);
      }
    }
  },
});

export const syncRunState = mutation({
  args: { run: v.any(), syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const run = asRecord(args.run);
    const agents = Object.values(asRecord(run.agents));
    const bots = Object.values(asRecord(run.bots));
    const games = Object.values(asRecord(run.games)).map((item) => {
      const record = asRecord(item);
      record.actions = [];
      return record;
    });
    const offers = Object.values(asRecord(run.offers));
    const purchases = Object.values(asRecord(run.purchases));
    const reviews = Object.values(asRecord(run.reviews));
    const comments = Object.values(asRecord(run.comments));
    const leaderboard = {
      agents: agents
        .map((agent) => ({
          agentId: String(asRecord(agent).agent_id ?? ""),
          bestBotId: stringOrNull(asRecord(agent).best_bot_id),
          bestRating: Number(asRecord(agent).best_rating_score ?? asRecord(agent).best_elo ?? 0),
          status: String(asRecord(agent).status ?? "unknown"),
        }))
        .sort((left, right) => right.bestRating - left.bestRating),
      bots: bots
        .map((bot) => ({
          botId: String(asRecord(bot).bot_id ?? ""),
          agentId: String(asRecord(bot).agent_id ?? ""),
          rating: Number(asRecord(bot).rating_score ?? asRecord(bot).elo ?? 0),
          active: Boolean(asRecord(bot).active),
          name: String(asRecord(bot).name ?? ""),
        }))
        .sort((left, right) => right.rating - left.rating),
    };
    await syncRunSummary.handler(ctx, {
      summary: {
        runId: String(run.run_id ?? ""),
        name: String(asRecord(run.config).name ?? ""),
        description: String(asRecord(run.config).description ?? ""),
        status: String(run.status ?? "unknown"),
        createdAt: parseTimestamp(run.created_at, Date.now()),
        startedAt:
          run.started_at === null || run.started_at === undefined
            ? undefined
            : parseTimestamp(run.started_at, Date.now()),
        finishedAt:
          run.finished_at === null || run.finished_at === undefined
            ? undefined
            : parseTimestamp(run.finished_at, Date.now()),
        updatedAt: Date.now(),
        agentCount: agents.length,
        botCount: bots.length,
        activeBotCount: bots.filter((bot) => Boolean(asRecord(bot).active)).length,
        gameCount: games.length,
        offerCount: offers.length,
        purchaseCount: purchases.length,
        reviewCount: reviews.length,
        bestAgentId: leaderboard.agents[0]?.agentId ?? undefined,
        bestRating: leaderboard.agents[0]?.bestRating ?? undefined,
        bestElo: leaderboard.agents[0]?.bestRating ?? undefined,
        config: run.config ?? {},
        finalScores: run.final_scores ?? {},
        payouts: run.payouts ?? {},
        snapshotPayload: {
          leaderboard: {
            agents: leaderboard.agents.slice(0, 32),
            bots: leaderboard.bots.slice(0, 64),
          },
          counts: {
            agentCount: agents.length,
            botCount: bots.length,
            activeBotCount: bots.filter((bot) => Boolean(asRecord(bot).active)).length,
            gameCount: games.length,
            offerCount: offers.length,
            purchaseCount: purchases.length,
            reviewCount: reviews.length,
          },
        },
      },
      syncToken: args.syncToken,
    });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "agents", items: agents, removedIds: [], syncToken: args.syncToken });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "bots", items: bots, removedIds: [], syncToken: args.syncToken });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "games", items: games, removedIds: [], syncToken: args.syncToken });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "offers", items: offers, removedIds: [], syncToken: args.syncToken });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "purchases", items: purchases, removedIds: [], syncToken: args.syncToken });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "reviews", items: reviews, removedIds: [], syncToken: args.syncToken });
    await syncRunChunk.handler(ctx, { runId: String(run.run_id ?? ""), kind: "comments", items: comments, removedIds: [], syncToken: args.syncToken });
  },
});

export const appendEvent = mutation({
  args: { event: v.any(), syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const event = asRecord(args.event);
    await ctx.db.insert("goaRunEvents", {
      runId: String(event.run_id ?? ""),
      kind: String(event.kind ?? "unknown"),
      createdAt: parseTimestamp(event.created_at, Date.now()),
      payloadJson: stringify(event.payload ?? {}),
    });
  },
});

export const batchSync = mutation({
  args: {
    run: v.optional(v.any()),
    events: v.optional(v.array(v.any())),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    if (args.run !== undefined) {
      await syncRunState.handler(ctx, { run: args.run, syncToken: args.syncToken });
    }
    for (const event of args.events ?? []) {
      await appendEvent.handler(ctx, { event, syncToken: args.syncToken });
    }
  },
});

const RUN_DELETE_TARGETS = [
  ["goaRunSnapshots", "by_run_id_created_at"],
  ["goaRunEvents", "by_run_id_created_at"],
  ["goaRunAgents", "by_run_id_agent_id"],
  ["goaRunBots", "by_run_id_bot_id"],
  ["goaRunGames", "by_run_id_started_at"],
  ["goaRunOffers", "by_run_id_offer_id"],
  ["goaRunPurchases", "by_run_id_purchase_id"],
  ["goaRunReviews", "by_run_id_review_id"],
  ["goaRunComments", "by_run_id_comment_id"],
  ["goaRunCommentReactions", "by_run_id_comment_id"],
  ["goaRunControls", "by_run_id"],
  ["goaRunSandboxes", "by_run_id_sandbox_id"],
  ["goaRunBotSubmissionQueue", "by_run_id_submission_id"],
  ["goaRunRuntimeLogs", "by_run_id_created_at"],
  ["goaRunAgentSteers", "by_run_id_steer_id"],
] as const;

async function deleteRowsForRun(
  ctx: any,
  table: string,
  index: string,
  runId: string,
  limit: number,
) {
  const rows = await ctx.db.query(table).withIndex(index, (q: any) => q.eq("runId", runId)).take(limit);
  for (const row of rows) {
    await ctx.db.delete(row._id);
  }
  return rows.length;
}

async function hasRowsForRun(ctx: any, runId: string) {
  const run = await ctx.db
    .query("goaRuns")
    .withIndex("by_run_id", (q) => q.eq("runId", runId))
    .unique();
  if (run) {
    return true;
  }
  for (const [table, index] of RUN_DELETE_TARGETS) {
    const rows = await ctx.db.query(table).withIndex(index, (q: any) => q.eq("runId", runId)).take(1);
    if (rows.length > 0) {
      return true;
    }
  }
  return false;
}

async function deleteRunChunkInternal(ctx: any, runId: string, limit: number) {
  let remainingLimit = Math.max(1, Math.min(Math.floor(limit), 2000));
  let deleted = 0;
  const run = await ctx.db
    .query("goaRuns")
    .withIndex("by_run_id", (q) => q.eq("runId", runId))
    .unique();
  if (run && remainingLimit > 0) {
    await ctx.db.delete(run._id);
    deleted += 1;
    remainingLimit -= 1;
  }
  for (const [table, index] of RUN_DELETE_TARGETS) {
    if (remainingLimit <= 0) {
      break;
    }
    const removed = await deleteRowsForRun(ctx, table, index, runId, remainingLimit);
    deleted += removed;
    remainingLimit -= removed;
  }
  return {
    deleted,
    remaining: await hasRowsForRun(ctx, runId),
  };
}

export const deleteRunChunk = mutation({
  args: {
    runId: v.string(),
    limit: v.optional(v.number()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    return await deleteRunChunkInternal(ctx, args.runId, args.limit ?? 500);
  },
});

export const deleteRun = mutation({
  args: { runId: v.string(), syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    return await deleteRunChunkInternal(ctx, args.runId, 500);
  },
});

export const resetAll = mutation({
  args: { syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    for (const table of [
      "goaRuns",
      "goaRunSnapshots",
      "goaRunEvents",
      "goaRunAgents",
      "goaRunBots",
      "goaRunGames",
      "goaRunOffers",
      "goaRunPurchases",
      "goaRunReviews",
      "goaRunComments",
      "goaRunCommentReactions",
      "goaRunControls",
      "goaRunSandboxes",
      "goaRunBotSubmissionQueue",
      "goaRunRuntimeLogs",
      "goaRunAgentSteers",
    ] as const) {
      const rows = await ctx.db.query(table).collect();
      for (const row of rows) {
        await ctx.db.delete(row._id);
      }
    }
  },
});

export const listRuns = query({
  args: {},
  handler: async (ctx) => {
    const now = Date.now();
    const runs = await ctx.db.query("goaRuns").withIndex("by_updated_at").order("desc").take(100);
    const [controls, sandboxesByRun] = await Promise.all([
      Promise.all(
        runs.map((run) =>
          ctx.db
            .query("goaRunControls")
            .withIndex("by_run_id", (q) => q.eq("runId", run.runId))
            .unique(),
        ),
      ),
      Promise.all(
        runs.map((run) =>
          ctx.db
            .query("goaRunSandboxes")
            .withIndex("by_run_id_last_heartbeat_at", (q) => q.eq("runId", run.runId))
            .order("desc")
            .take(16),
        ),
      ),
    ]);
    return runs.map((run, index) => {
      const lifecycle = deriveRunLifecycle(run, controls[index], sandboxesByRun[index], now);
      return {
        runId: run.runId,
        name: run.name,
        description: run.description,
        status: lifecycle.status,
        createdAt: run.createdAt,
        startedAt: run.startedAt ?? null,
        finishedAt: lifecycle.finishedAt,
        updatedAt: run.updatedAt,
        agentCount: run.agentCount,
        botCount: run.botCount,
        activeBotCount: run.activeBotCount,
        gameCount: run.gameCount,
        offerCount: run.offerCount,
        purchaseCount: run.purchaseCount,
        reviewCount: run.reviewCount,
        bestAgentId: run.bestAgentId ?? null,
        bestRating: run.bestRating ?? run.bestElo ?? null,
        config: parseJson(run.configJson),
        finalScores: parseJson(run.finalScoresJson),
        payouts: parseJson(run.payoutsJson),
      };
    });
  },
});

export const getRunSummary = query({
  args: { runId: v.string() },
  handler: async (ctx, args) => {
    const now = Date.now();
    const run = await ctx.db
      .query("goaRuns")
      .withIndex("by_run_id", (q) => q.eq("runId", args.runId))
      .unique();
    if (!run) {
      return null;
    }
    const [control, sandboxes] = await Promise.all([
      ctx.db
        .query("goaRunControls")
        .withIndex("by_run_id", (q) => q.eq("runId", args.runId))
        .unique(),
      ctx.db
        .query("goaRunSandboxes")
        .withIndex("by_run_id_last_heartbeat_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(16),
    ]);
    const lifecycle = deriveRunLifecycle(run, control, sandboxes, now);
    return {
      runId: run.runId,
      name: run.name,
      description: run.description,
      status: lifecycle.status,
      createdAt: run.createdAt,
      startedAt: run.startedAt ?? null,
      finishedAt: lifecycle.finishedAt,
      updatedAt: run.updatedAt,
      agentCount: run.agentCount,
      botCount: run.botCount,
      activeBotCount: run.activeBotCount,
      gameCount: run.gameCount,
      offerCount: run.offerCount,
      purchaseCount: run.purchaseCount,
      reviewCount: run.reviewCount,
      bestAgentId: run.bestAgentId ?? null,
      bestRating: run.bestRating ?? run.bestElo ?? null,
      config: parseJson(run.configJson),
      finalScores: parseJson(run.finalScoresJson),
      payouts: parseJson(run.payoutsJson),
    };
  },
});

export const getRunDashboard = query({
  args: {
    runId: v.string(),
    snapshotLimit: v.optional(v.number()),
    eventLimit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const now = Date.now();
    const run = await ctx.db
      .query("goaRuns")
      .withIndex("by_run_id", (q) => q.eq("runId", args.runId))
      .unique();
    if (!run) {
      return null;
    }

    const snapshotLimit = args.snapshotLimit ?? 2000;
    const eventLimit = args.eventLimit ?? 200;
    const [snapshots, events, runState, control, sandboxes] = await Promise.all([
      ctx.db
        .query("goaRunSnapshots")
        .withIndex("by_run_id_created_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(snapshotLimit),
      ctx.db
        .query("goaRunEvents")
        .withIndex("by_run_id_created_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(eventLimit),
      collectRunState(ctx, args.runId),
      ctx.db
        .query("goaRunControls")
        .withIndex("by_run_id", (q) => q.eq("runId", args.runId))
        .unique(),
      ctx.db
        .query("goaRunSandboxes")
        .withIndex("by_run_id_last_heartbeat_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(16),
    ]);
    const lifecycle = deriveRunLifecycle(run, control, sandboxes, now);
    const config = parseJson<GenericRecord>(run.configJson);
    overlayAgentSandboxState(runState.state.agents as GenericRecord[], sandboxes, config, now);

    const normalizedEvents = events
      .map((event) => ({
        eventId: String(event._id),
        kind: event.kind,
        createdAt: event.createdAt,
        payload: parseJson<GenericRecord>(event.payloadJson),
      }))
      .reverse();

    return {
      run: {
        runId: run.runId,
        name: run.name,
        description: run.description,
        status: lifecycle.status,
        createdAt: run.createdAt,
        startedAt: run.startedAt ?? null,
        finishedAt: lifecycle.finishedAt,
        updatedAt: run.updatedAt,
        agentCount: run.agentCount,
        botCount: run.botCount,
        activeBotCount: run.activeBotCount,
        gameCount: run.gameCount,
        offerCount: run.offerCount,
        purchaseCount: run.purchaseCount,
        reviewCount: run.reviewCount,
        bestAgentId: run.bestAgentId ?? null,
        bestRating: run.bestRating ?? run.bestElo ?? null,
        config,
        state: runState.state,
        leaderboard: runState.leaderboard,
        finalScores: parseJson(run.finalScoresJson),
        payouts: parseJson(run.payoutsJson),
      },
      snapshots: snapshots
        .map((snapshot) => ({
          createdAt: snapshot.createdAt,
          status: snapshot.status,
          bestAgentId: snapshot.bestAgentId ?? null,
          bestRating: snapshot.bestRating ?? snapshot.bestElo ?? null,
          botCount: snapshot.botCount,
          activeBotCount: snapshot.activeBotCount,
          gameCount: snapshot.gameCount,
          offerCount: snapshot.offerCount,
          payload: parseJson(snapshot.payloadJson),
        }))
        .reverse(),
      events: normalizedEvents,
      comments: buildComments(runState.state.comments, runState.state.reviews, normalizedEvents),
      feedMessages: buildFeedMessages(runState.state.comments),
      chatMessages: buildChatMessages(normalizedEvents),
    };
  },
});

export const getRunDashboardSampled = query({
  args: {
    runId: v.string(),
    sampleSize: v.optional(v.number()),
    eventLimit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const now = Date.now();
    const run = await ctx.db
      .query("goaRuns")
      .withIndex("by_run_id", (q) => q.eq("runId", args.runId))
      .unique();
    if (!run) {
      return null;
    }

    const sampleSize = Math.max(3, args.sampleSize ?? 180);
    const eventLimit = args.eventLimit ?? 200;
    const [firstSnapshots, lastSnapshots, events, runState, control, sandboxes] = await Promise.all([
      ctx.db
        .query("goaRunSnapshots")
        .withIndex("by_run_id_created_at", (q) => q.eq("runId", args.runId))
        .take(1),
      ctx.db
        .query("goaRunSnapshots")
        .withIndex("by_run_id_created_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(1),
      ctx.db
        .query("goaRunEvents")
        .withIndex("by_run_id_created_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(eventLimit),
      collectRunState(ctx, args.runId),
      ctx.db
        .query("goaRunControls")
        .withIndex("by_run_id", (q) => q.eq("runId", args.runId))
        .unique(),
      ctx.db
        .query("goaRunSandboxes")
        .withIndex("by_run_id_last_heartbeat_at", (q) => q.eq("runId", args.runId))
        .order("desc")
        .take(16),
    ]);
    const first = firstSnapshots[0];
    const last = lastSnapshots[0];
    const bucketRows = [];
    if (first && last && first._id !== last._id) {
      const bucketCount = sampleSize - 2;
      const start = first.createdAt;
      const end = last.createdAt;
      const width = Math.max(1, Math.ceil((end - start) / Math.max(1, bucketCount)));
      for (let index = 0; index < bucketCount; index += 1) {
        const bucketStart = start + index * width;
        const bucketEnd = index === bucketCount - 1 ? end : Math.min(end, bucketStart + width);
        bucketRows.push(
          ctx.db
            .query("goaRunSnapshots")
            .withIndex("by_run_id_created_at", (q: any) =>
              q.eq("runId", args.runId).gte("createdAt", bucketStart).lt("createdAt", bucketEnd),
            )
            .take(1),
        );
      }
    }
    const sampledBuckets = bucketRows.length > 0 ? await Promise.all(bucketRows) : [];
    const snapshotMap = new Map<string, any>();
    if (first) {
      snapshotMap.set(String(first._id), first);
    }
    for (const rows of sampledBuckets) {
      const row = rows[0];
      if (row) {
        snapshotMap.set(String(row._id), row);
      }
    }
    if (last) {
      snapshotMap.set(String(last._id), last);
    }
    const snapshots = Array.from(snapshotMap.values()).sort((left, right) => left.createdAt - right.createdAt);
    const lifecycle = deriveRunLifecycle(run, control, sandboxes, now);
    const config = parseJson<GenericRecord>(run.configJson);
    overlayAgentSandboxState(runState.state.agents as GenericRecord[], sandboxes, config, now);

    const normalizedEvents = events
      .map((event) => ({
        eventId: String(event._id),
        kind: event.kind,
        createdAt: event.createdAt,
        payload: parseJson<GenericRecord>(event.payloadJson),
      }))
      .reverse();

    return {
      run: {
        runId: run.runId,
        name: run.name,
        description: run.description,
        status: lifecycle.status,
        createdAt: run.createdAt,
        startedAt: run.startedAt ?? null,
        finishedAt: lifecycle.finishedAt,
        updatedAt: run.updatedAt,
        agentCount: run.agentCount,
        botCount: run.botCount,
        activeBotCount: run.activeBotCount,
        gameCount: run.gameCount,
        offerCount: run.offerCount,
        purchaseCount: run.purchaseCount,
        reviewCount: run.reviewCount,
        bestAgentId: run.bestAgentId ?? null,
        bestRating: run.bestRating ?? run.bestElo ?? null,
        config,
        state: runState.state,
        leaderboard: runState.leaderboard,
        finalScores: parseJson(run.finalScoresJson),
        payouts: parseJson(run.payoutsJson),
      },
      snapshots: snapshots.map((snapshot) => ({
        createdAt: snapshot.createdAt,
        status: snapshot.status,
        bestAgentId: snapshot.bestAgentId ?? null,
        bestRating: snapshot.bestRating ?? snapshot.bestElo ?? null,
        botCount: snapshot.botCount,
        activeBotCount: snapshot.activeBotCount,
        gameCount: snapshot.gameCount,
        offerCount: snapshot.offerCount,
        payload: parseJson(snapshot.payloadJson),
      })),
      events: normalizedEvents,
      comments: buildComments(runState.state.comments, runState.state.reviews, normalizedEvents),
      feedMessages: buildFeedMessages(runState.state.comments),
      chatMessages: buildChatMessages(normalizedEvents),
    };
  },
});
