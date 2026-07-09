import { mutation, query } from "./_generated/server";
import { v } from "convex/values";

type GenericRecord = Record<string, unknown>;

const BASE_MU = 25;
const BASE_SIGMA = 25 / 3;
const BASE_ELO = 1000;

function asRecord(value: unknown): GenericRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as GenericRecord) : {};
}

function stringify(value: unknown): string {
  return JSON.stringify(value ?? null);
}

function parseJson<T>(value: string | undefined): T | null {
  if (!value) {
    return null;
  }
  return JSON.parse(value) as T;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

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

function compactMatchPayload(match: GenericRecord, excludeActions: boolean = false) {
  const payload: GenericRecord = {
    participants: Array.isArray(match.participants) ? match.participants : [],
    reason: stringOrNull(match.reason),
    max_rounds_reached: Boolean(match.max_rounds_reached),
    loser_bot_id: stringOrNull(match.loser_bot_id),
    bot_a_id: stringOrNull(match.bot_a_id),
    bot_b_id: stringOrNull(match.bot_b_id),
    agent_a_id: stringOrNull(match.agent_a_id),
    agent_b_id: stringOrNull(match.agent_b_id),
  };
  // Only include full action traces when explicitly requested.
  // Storing actions in summaries inflates writes by 10-100x per game
  // and makes downstream reads (dashboard, analytics) proportionally heavier.
  if (!excludeActions) {
    payload.actions = Array.isArray(match.actions) ? match.actions : [];
  }
  return payload;
}

function hydrateGamePayload(row: any, includeActions: boolean = true) {
  const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
  return {
    game_id: String((payload.game_id as string | undefined) ?? row.gameId),
    run_id: String((payload.run_id as string | undefined) ?? row.runId),
    status: String((payload.status as string | undefined) ?? row.status ?? "finished"),
    table_size: Number((payload.table_size as number | undefined) ?? row.tableSize ?? 0),
    round_count: Number((payload.round_count as number | undefined) ?? row.roundCount ?? 0),
    max_rounds_reached: Boolean(payload.max_rounds_reached),
    participants: Array.isArray(payload.participants) ? payload.participants : [],
    winner_bot_id: stringOrNull(payload.winner_bot_id) ?? stringOrNull(row.winnerBotId),
    loser_bot_id: stringOrNull(payload.loser_bot_id),
    reason: stringOrNull(payload.reason),
    actions: includeActions && Array.isArray(payload.actions) ? payload.actions : [],
    started_at: payload.started_at ?? new Date(row.startedAt).toISOString(),
    finished_at:
      row.finishedAt === undefined || row.finishedAt === null
        ? payload.finished_at ?? null
        : payload.finished_at ?? new Date(row.finishedAt).toISOString(),
    duration_seconds:
      row.durationSeconds === undefined || row.durationSeconds === null
        ? numberOrNull(payload.duration_seconds)
        : Number(row.durationSeconds),
    bot_a_id: stringOrNull(payload.bot_a_id),
    bot_b_id: stringOrNull(payload.bot_b_id),
    agent_a_id: stringOrNull(payload.agent_a_id),
    agent_b_id: stringOrNull(payload.agent_b_id),
  };
}

function compactRuntimeLogPayload(block: GenericRecord, createdAt: number) {
  return {
    collapsed: Boolean(block.collapsed),
    streaming: Boolean(block.streaming),
    updated_at:
      typeof block.updated_at === "string" && block.updated_at.length > 0
        ? block.updated_at
        : new Date(createdAt).toISOString(),
  };
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

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-6)}`;
}

function runStatus(status: string, finishedAt?: number | null): string {
  if (status === "failed") {
    return "failed";
  }
  if (finishedAt !== undefined && finishedAt !== null) {
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

function ownsAgentSandbox(payload: GenericRecord, sandboxId: string): boolean {
  const currentSandboxId = stringOrNull(payload.sandbox_id);
  return currentSandboxId === null || currentSandboxId === sandboxId;
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

function derivedRunLifecycle(
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
  const config = parseJson<GenericRecord>(run.configJson ?? "") ?? {};
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

function parseRunConfig(run: { configJson?: string }) {
  return parseJson<GenericRecord>(run.configJson ?? "") ?? {};
}

function parseRunFinalScores(run: { finalScoresJson?: string }, agentRows: any[]) {
  const stored = asRecord(parseJson<GenericRecord>(run.finalScoresJson ?? "") ?? {});
  const scores: Record<string, number> = {};
  for (const row of agentRows) {
    const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
    const agentId = String(row.agentId ?? payload.agent_id ?? "");
    if (!agentId) {
      continue;
    }
    const explicit = stored[agentId];
    scores[agentId] =
      typeof explicit === "number" && Number.isFinite(explicit)
        ? explicit
        : Number(row.bestRating ?? payload.best_rating_score ?? payload.best_elo ?? 0);
  }
  return scores;
}

function computeProjectedPayouts(
  baseScores: Record<string, number>,
  purchaseRows: any[],
  settlementMode: string,
  explicitPayouts: GenericRecord | null,
) {
  const payouts: Record<string, number> = {};
  for (const [agentId, score] of Object.entries(baseScores)) {
    payouts[agentId] = Number(score ?? 0);
  }
  if (explicitPayouts && Object.keys(explicitPayouts).length > 0) {
    for (const agentId of Object.keys(baseScores)) {
      const explicit = explicitPayouts[agentId];
      payouts[agentId] =
        typeof explicit === "number" && Number.isFinite(explicit)
          ? explicit
          : Number(baseScores[agentId] ?? 0);
    }
    return payouts;
  }
  for (const row of purchaseRows) {
    const payload = normalizePurchasePayload(parseJson<GenericRecord>(row.payloadJson) ?? {});
    const buyer = String(payload.buyer_agent_id ?? "");
    const seller = String(payload.seller_agent_id ?? "");
    if (!buyer || !seller) {
      continue;
    }
    const buyerBase = Number(baseScores[buyer] ?? 0);
    const transfer = buyerBase * (Number(payload.price_pct ?? 0) / 100);
    if (settlementMode === "net") {
      payouts[buyer] = Number(payouts[buyer] ?? buyerBase) - transfer;
    }
    payouts[seller] = Number(payouts[seller] ?? Number(baseScores[seller] ?? 0)) + transfer;
  }
  return payouts;
}

function buildAgentAnalyticsRows(
  run: { configJson?: string; finalScoresJson?: string; payoutsJson?: string },
  agentRows: any[],
  purchaseRows: any[],
) {
  const config = parseRunConfig(run);
  const settlementMode = String(config.settlement_mode ?? "net");
  const baseScores = parseRunFinalScores(run, agentRows);
  const explicitPayouts = asRecord(parseJson<GenericRecord>(run.payoutsJson ?? "") ?? {});
  const payouts = computeProjectedPayouts(baseScores, purchaseRows, settlementMode, explicitPayouts);
  const rows = agentRows.map((row: any) => {
    const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
    const agentId = String(row.agentId ?? payload.agent_id ?? "");
    const bestRating = Number(row.bestRating ?? payload.best_rating_score ?? payload.best_elo ?? 0);
    const projectedPayout = Number(payouts[agentId] ?? bestRating);
    return {
      agent_id: agentId,
      status: String(row.status ?? payload.status ?? "idle"),
      best_rating: bestRating,
      best_bot_id: stringOrNull(payload.best_bot_id),
      projected_payout: projectedPayout,
      equity_delta: projectedPayout - Number(baseScores[agentId] ?? bestRating),
      tournament_rank: 0,
      projected_rank: 0,
    };
  });
  const tournamentOrder = [...rows].sort(
    (left, right) => right.best_rating - left.best_rating || left.agent_id.localeCompare(right.agent_id),
  );
  const projectedOrder = [...rows].sort(
    (left, right) =>
      right.projected_payout - left.projected_payout ||
      right.best_rating - left.best_rating ||
      left.agent_id.localeCompare(right.agent_id),
  );
  const tournamentRanks = new Map(tournamentOrder.map((row, index) => [row.agent_id, index + 1]));
  const projectedRanks = new Map(projectedOrder.map((row, index) => [row.agent_id, index + 1]));
  for (const row of rows) {
    row.tournament_rank = tournamentRanks.get(row.agent_id) ?? rows.length;
    row.projected_rank = projectedRanks.get(row.agent_id) ?? rows.length;
  }
  return {
    rows: projectedOrder,
    totalAgents: rows.length,
    settlementMode,
  };
}

function giniCoefficient(values: number[]) {
  if (!values.length || values.every((value) => value === 0)) {
    return 0;
  }
  const sorted = [...values].sort((left, right) => left - right);
  const total = sorted.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total === 0) {
    return 0;
  }
  const cumsum = sorted.reduce((sum, value, index) => sum + (2 * index - sorted.length + 1) * value, 0);
  return cumsum / (sorted.length * total);
}

function countTopActor(counter: Map<string, number>) {
  let bestId: string | null = null;
  let bestCount = -1;
  for (const [id, count] of counter.entries()) {
    if (count > bestCount || (count === bestCount && id.localeCompare(bestId ?? "") > 0)) {
      bestId = id;
      bestCount = count;
    }
  }
  return bestId;
}

function buildRunAnalysisSummary(
  run: { runId: string; status?: string; updatedAt?: number | null; createdAt?: number | null; startedAt?: number | null; configJson?: string },
  status: string,
  agentRows: any[],
  botRows: any[],
  offerRows: any[],
  purchaseRows: any[],
  reviewRows: any[],
  commentRows: any[],
  gameRows: any[],
  finishedAt: number | null,
) {
  const config = parseRunConfig(run);
  const modelByAgentId = new Map<string, string>();
  const configAgents = Array.isArray(config.agents) ? config.agents : [];
  for (const item of configAgents) {
    const payload = asRecord(item);
    const agentId = String(payload.agent_id ?? "");
    const model = stringOrNull(payload.model);
    if (agentId && model) {
      modelByAgentId.set(agentId, model);
    }
  }

  const agentElos: number[] = [];
  const actionCounts = new Map<string, { raises: number; passive: number }>();

  for (const row of agentRows) {
    const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
    const agentId = String(row.agentId ?? payload.agent_id ?? "");
    if (!agentId) {
      continue;
    }
    const bestElo = Number(payload.best_elo ?? row.bestRating ?? payload.best_rating_score ?? 0);
    agentElos.push(bestElo);
    actionCounts.set(agentId, { raises: 0, passive: 0 });
  }

  for (const row of gameRows) {
    const game = hydrateGamePayload(row, true);
    if (String(game.status ?? "") !== "finished") {
      continue;
    }
    const participants = Array.isArray(game.participants) ? game.participants : [];
    const seatToAgentId = new Map<number, string>();
    for (const participant of participants) {
      const payload = asRecord(participant);
      const seat = Number(payload.seat ?? -1);
      const agentId = String(payload.agent_id ?? "");
      if (seat >= 0 && agentId) {
        seatToAgentId.set(seat, agentId);
        if (!actionCounts.has(agentId)) {
          actionCounts.set(agentId, { raises: 0, passive: 0 });
        }
      }
    }
    const actions = Array.isArray(game.actions) ? game.actions : [];
    for (const rawAction of actions) {
      const action = asRecord(rawAction);
      const kind = String(action.kind ?? "");
      if (kind !== "raise_to" && kind !== "check_call" && kind !== "fold") {
        continue;
      }
      const seat = Number(action.seat ?? -1);
      const agentId = seatToAgentId.get(seat);
      if (!agentId) {
        continue;
      }
      const counts = actionCounts.get(agentId) ?? { raises: 0, passive: 0 };
      if (kind === "raise_to") {
        counts.raises += 1;
      } else {
        counts.passive += 1;
      }
      actionCounts.set(agentId, counts);
    }
  }

  const aggressionValues = Array.from(actionCounts.values()).map((counts) =>
    counts.passive > 0 ? counts.raises / counts.passive : 0,
  );

  const sellerCounts = new Map<string, number>();
  const buyerCounts = new Map<string, number>();
  let priceSum = 0;
  let sameModelPurchases = 0;
  let comparablePurchases = 0;

  for (const row of offerRows) {
    const offer = normalizeOfferPayload(parseJson<GenericRecord>(row.payloadJson) ?? {});
    const sellerId = String(offer.seller_agent_id ?? "");
    if (sellerId) {
      sellerCounts.set(sellerId, Number(sellerCounts.get(sellerId) ?? 0) + 1);
    }
    priceSum += Number(offer.price_pct ?? 0);
  }

  for (const row of purchaseRows) {
    const purchase = normalizePurchasePayload(parseJson<GenericRecord>(row.payloadJson) ?? {});
    const buyerId = String(purchase.buyer_agent_id ?? "");
    if (buyerId) {
      buyerCounts.set(buyerId, Number(buyerCounts.get(buyerId) ?? 0) + 1);
    }
    const buyerModel = modelByAgentId.get(buyerId);
    const sellerModel = modelByAgentId.get(String(purchase.seller_agent_id ?? ""));
    if (!buyerModel || !sellerModel) {
      continue;
    }
    comparablePurchases += 1;
    if (buyerModel === sellerModel) {
      sameModelPurchases += 1;
    }
  }

  const updatedAt = Math.max(
    Number(run.updatedAt ?? 0),
    Number(finishedAt ?? 0),
    Number(run.startedAt ?? 0),
    Number(run.createdAt ?? 0),
  );

  return {
    runId: run.runId,
    status,
    updatedAt,
    giniCoefficient: giniCoefficient(agentElos),
    avgAggression:
      aggressionValues.length > 0
        ? aggressionValues.reduce((sum, value) => sum + value, 0) / aggressionValues.length
        : 0,
    botsSubmitted: botRows.length,
    chatMessageCount: commentRows.length,
    topAgentElo: agentElos.length > 0 ? Math.max(...agentElos) : null,
    marketplace: {
      totalOffers: offerRows.length,
      totalPurchases: purchaseRows.length,
      totalReviews: reviewRows.length,
      avgPricePct: offerRows.length > 0 ? priceSum / offerRows.length : 0,
      mostActiveSeller: countTopActor(sellerCounts),
      mostActiveBuyer: countTopActor(buyerCounts),
      sameModelPurchasePct: comparablePurchases > 0 ? (sameModelPurchases / comparablePurchases) * 100 : null,
    },
  };
}

function gameMatchesFilters(
  game: ReturnType<typeof hydrateGamePayload>,
  filters: {
    agentId?: string | null;
    botId?: string | null;
    winnerBotId?: string | null;
    status?: string | null;
    reasonContains?: string | null;
  },
) {
  if (filters.status && String(game.status ?? "") !== filters.status) {
    return false;
  }
  if (filters.winnerBotId && String(game.winner_bot_id ?? "") !== filters.winnerBotId) {
    return false;
  }
  if (filters.reasonContains) {
    const haystack = String(game.reason ?? "").toLowerCase();
    if (!haystack.includes(filters.reasonContains.toLowerCase())) {
      return false;
    }
  }
  const participants = Array.isArray(game.participants) ? game.participants : [];
  if (filters.botId) {
    const matchedBot = participants.some((participant: any) => String(participant?.bot_id ?? "") === filters.botId);
    if (!matchedBot && String(game.bot_a_id ?? "") !== filters.botId && String(game.bot_b_id ?? "") !== filters.botId) {
      return false;
    }
  }
  if (filters.agentId) {
    const matchedAgent = participants.some((participant: any) => String(participant?.agent_id ?? "") === filters.agentId);
    if (
      !matchedAgent &&
      String(game.agent_a_id ?? "") !== filters.agentId &&
      String(game.agent_b_id ?? "") !== filters.agentId
    ) {
      return false;
    }
  }
  return true;
}

async function findRun(ctx: any, runId: string) {
  return ctx.db.query("goaRuns").withIndex("by_run_id", (q: any) => q.eq("runId", runId)).unique();
}

async function findRunControl(ctx: any, runId: string) {
  return ctx.db
    .query("goaRunControls")
    .withIndex("by_run_id", (q: any) => q.eq("runId", runId))
    .unique();
}

async function findAgent(ctx: any, runId: string, agentId: string) {
  return ctx.db
    .query("goaRunAgents")
    .withIndex("by_run_id_agent_id", (q: any) => q.eq("runId", runId).eq("agentId", agentId))
    .unique();
}

async function findBot(ctx: any, runId: string, botId: string) {
  return ctx.db
    .query("goaRunBots")
    .withIndex("by_run_id_bot_id", (q: any) => q.eq("runId", runId).eq("botId", botId))
    .unique();
}

async function findGame(ctx: any, runId: string, gameId: string) {
  return ctx.db
    .query("goaRunGames")
    .withIndex("by_run_id_game_id", (q: any) => q.eq("runId", runId).eq("gameId", gameId))
    .unique();
}

async function findOffer(ctx: any, runId: string, offerId: string) {
  return ctx.db
    .query("goaRunOffers")
    .withIndex("by_run_id_offer_id", (q: any) => q.eq("runId", runId).eq("offerId", offerId))
    .unique();
}

async function findPurchase(ctx: any, runId: string, purchaseId: string) {
  return ctx.db
    .query("goaRunPurchases")
    .withIndex("by_run_id_purchase_id", (q: any) => q.eq("runId", runId).eq("purchaseId", purchaseId))
    .unique();
}

async function findReview(ctx: any, runId: string, reviewId: string) {
  return ctx.db
    .query("goaRunReviews")
    .withIndex("by_run_id_review_id", (q: any) => q.eq("runId", runId).eq("reviewId", reviewId))
    .unique();
}

async function findComment(ctx: any, runId: string, commentId: string) {
  return ctx.db
    .query("goaRunComments")
    .withIndex("by_run_id_comment_id", (q: any) => q.eq("runId", runId).eq("commentId", commentId))
    .unique();
}

async function findCommentReaction(ctx: any, runId: string, commentId: string, emoji: string, authorAgentId: string) {
  return ctx.db
    .query("goaRunCommentReactions")
    .withIndex("by_run_id_comment_id_emoji_author_agent_id", (q: any) =>
      q.eq("runId", runId).eq("commentId", commentId).eq("emoji", emoji).eq("authorAgentId", authorAgentId),
    )
    .unique();
}

async function findSandbox(ctx: any, runId: string, sandboxId: string) {
  return ctx.db
    .query("goaRunSandboxes")
    .withIndex("by_run_id_sandbox_id", (q: any) => q.eq("runId", runId).eq("sandboxId", sandboxId))
    .unique();
}

async function findSubmission(ctx: any, runId: string, submissionId: string) {
  return ctx.db
    .query("goaRunBotSubmissionQueue")
    .withIndex("by_run_id_submission_id", (q: any) => q.eq("runId", runId).eq("submissionId", submissionId))
    .unique();
}

async function findRuntimeLog(ctx: any, runId: string, logId: string) {
  return ctx.db
    .query("goaRunRuntimeLogs")
    .withIndex("by_run_id_log_id", (q: any) => q.eq("runId", runId).eq("logId", logId))
    .unique();
}

async function findAgentSteer(ctx: any, runId: string, steerId: string) {
  return ctx.db
    .query("goaRunAgentSteers")
    .withIndex("by_run_id_steer_id", (q: any) => q.eq("runId", runId).eq("steerId", steerId))
    .unique();
}

async function appendRunEvent(
  ctx: any,
  runId: string,
  kind: string,
  createdAt: number,
  payload: unknown,
) {
  await ctx.db.insert("goaRunEvents", {
    runId,
    kind,
    createdAt,
    payloadJson: stringify(payload),
  });
}

async function ensureRunSummary(
  ctx: any,
  runId: string,
  patch: {
    now: number;
    name?: string;
    description?: string;
    status?: string;
    createdAt?: number;
    startedAt?: number | null;
    finishedAt?: number | null;
    agentCount?: number;
    botCount?: number;
    activeBotCount?: number;
    gameCount?: number;
    offerCount?: number;
    purchaseCount?: number;
    reviewCount?: number;
    bestAgentId?: string | null;
    bestRating?: number | null;
    config?: unknown;
    finalScores?: unknown;
    payouts?: unknown;
  },
) {
  const existing = await findRun(ctx, runId);
  const document = {
    runId,
    name: patch.name ?? existing?.name ?? runId,
    description: patch.description ?? existing?.description ?? "",
    status: patch.status ?? existing?.status ?? "pending",
    createdAt: patch.createdAt ?? existing?.createdAt ?? patch.now,
    startedAt: patch.startedAt === undefined ? existing?.startedAt : patch.startedAt ?? undefined,
    finishedAt: patch.finishedAt === undefined ? existing?.finishedAt : patch.finishedAt ?? undefined,
    updatedAt: patch.now,
    agentCount: patch.agentCount ?? existing?.agentCount ?? 0,
    botCount: patch.botCount ?? existing?.botCount ?? 0,
    activeBotCount: patch.activeBotCount ?? existing?.activeBotCount ?? 0,
    gameCount: patch.gameCount ?? existing?.gameCount ?? 0,
    offerCount: patch.offerCount ?? existing?.offerCount ?? 0,
    purchaseCount: patch.purchaseCount ?? existing?.purchaseCount ?? 0,
    reviewCount: patch.reviewCount ?? existing?.reviewCount ?? 0,
    bestAgentId: patch.bestAgentId === undefined ? existing?.bestAgentId : patch.bestAgentId ?? undefined,
    bestRating: patch.bestRating === undefined ? existing?.bestRating : patch.bestRating ?? undefined,
    bestElo: patch.bestRating === undefined ? existing?.bestElo : patch.bestRating ?? undefined,
    stateJson: existing?.stateJson,
    leaderboardJson: existing?.leaderboardJson,
    configJson: stringify(patch.config ?? parseJson(existing?.configJson) ?? {}),
    finalScoresJson: stringify(patch.finalScores ?? parseJson(existing?.finalScoresJson) ?? {}),
    payoutsJson: stringify(patch.payouts ?? parseJson(existing?.payoutsJson) ?? {}),
  };
  if (existing) {
    await ctx.db.patch(existing._id, document);
  } else {
    await ctx.db.insert("goaRuns", document);
  }
  return document;
}

async function writeSnapshot(ctx: any, runId: string, createdAt: number) {
  const run = await findRun(ctx, runId);
  if (!run) {
    return;
  }
  const agents = await ctx.db
    .query("goaRunAgents")
    .withIndex("by_run_id_best_rating", (q: any) => q.eq("runId", runId))
    .order("desc")
    .take(32);
  const bots = await ctx.db
    .query("goaRunBots")
    .withIndex("by_run_id_rating", (q: any) => q.eq("runId", runId))
    .order("desc")
    .take(64);

  await ctx.db.insert("goaRunSnapshots", {
    runId,
    createdAt,
    status: runStatus(run.status, run.finishedAt),
    bestAgentId: run.bestAgentId ?? undefined,
    bestRating: run.bestRating ?? run.bestElo ?? undefined,
    bestElo: run.bestElo ?? undefined,
    botCount: run.botCount,
    activeBotCount: run.activeBotCount,
    gameCount: run.gameCount,
    offerCount: run.offerCount,
    payloadJson: stringify({
      leaderboard: {
        agents: agents.map((row: any) => {
          const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
          return {
            agentId: row.agentId,
            bestBotId: stringOrNull(payload.best_bot_id),
            bestRating: Number(payload.best_rating_score ?? payload.best_elo ?? 0),
            status: String(payload.status ?? row.status),
          };
        }),
        bots: bots.map((row: any) => ({
          botId: row.botId,
          agentId: row.agentId,
          rating: row.rating,
          active: row.active,
          name: row.name,
        })),
      },
      counts: {
        agentCount: run.agentCount,
        botCount: run.botCount,
        activeBotCount: run.activeBotCount,
        gameCount: run.gameCount,
        offerCount: run.offerCount,
        purchaseCount: run.purchaseCount,
        reviewCount: run.reviewCount,
      },
    }),
  });
}

function defaultAgentPayload(runId: string, agent: GenericRecord) {
  const agentId = String(agent.agent_id ?? "");
  return {
    agent_id: agentId,
    runtime: String(agent.runtime ?? "mock"),
    internet_access: Boolean(agent.internet_access),
    workspace: `/tmp/goa/${runId}/${agentId}`,
    sandbox_name: null,
    best_bot_id: null,
    best_rating_mu: BASE_MU,
    best_rating_sigma: BASE_SIGMA,
    best_rating_score: 0,
    best_elo: BASE_ELO,
    status: "idle",
    last_message: null,
    sandbox_id: null,
    sandbox_status: null,
    current_step_started_at: null,
    last_activity_at: null,
    last_output_at: null,
  };
}

function normalizeOfferPayload(payload: GenericRecord) {
  return {
    offer_id: String(payload.offer_id ?? ""),
    seller_agent_id: String(payload.seller_agent_id ?? payload.agent_id ?? ""),
    bot_id: stringOrNull(payload.bot_id) ?? "",
    title: String(payload.title ?? ""),
    description: String(payload.description ?? ""),
    evidence: String(payload.evidence ?? ""),
    price_pct: Number(payload.price_pct ?? 0),
    bundle_storage_id: String(payload.bundle_storage_id ?? payload.bundleStorageId ?? ""),
    bundle_bytes: Number(payload.bundle_bytes ?? payload.bundleBytes ?? 0),
    file_paths: Array.isArray(payload.file_paths)
      ? payload.file_paths.map((item) => String(item))
      : Array.isArray(payload.filePaths)
        ? payload.filePaths.map((item) => String(item))
        : [],
    status: String(payload.status ?? "active"),
    review_count: Number(payload.review_count ?? 0),
    created_at: payload.created_at,
    updated_at: payload.updated_at,
  };
}

function normalizePurchasePayload(payload: GenericRecord) {
  return {
    purchase_id: String(payload.purchase_id ?? ""),
    offer_id: String(payload.offer_id ?? ""),
    buyer_agent_id: String(payload.buyer_agent_id ?? ""),
    seller_agent_id: String(payload.seller_agent_id ?? ""),
    price_pct: Number(payload.price_pct ?? 0),
    bundle_storage_id: String(payload.bundle_storage_id ?? payload.bundleStorageId ?? ""),
    file_paths: Array.isArray(payload.file_paths)
      ? payload.file_paths.map((item) => String(item))
      : [],
    created_at: payload.created_at,
  };
}

async function assertWritable(ctx: any, runId: string) {
  const control = await findRunControl(ctx, runId);
  const metadata = parseJson<GenericRecord>(control?.metadataJson) ?? {};
  if (metadata.read_only || metadata.readOnly) {
    throw new Error("run is read-only");
  }
  if (control?.status === "stopping" || control?.status === "finished" || control?.status === "failed") {
    throw new Error("run is not writable");
  }
}

export const createRun = mutation({
  args: {
    config: v.any(),
    runId: v.optional(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const config = asRecord(args.config);
    const runId = args.runId ?? newId("run");
    const existingControl = await findRunControl(ctx, runId);

    const controlDocument = {
      runId,
      status: existingControl?.status ?? "pending",
      createdAt: existingControl?.createdAt ?? now,
      updatedAt: now,
      startedAt: existingControl?.startedAt,
      stopRequestedAt: existingControl?.stopRequestedAt,
      stoppedAt: existingControl?.stoppedAt,
      orchestratorId: existingControl?.orchestratorId,
      stopReason: existingControl?.stopReason,
      configJson: stringify(config),
      metadataJson: stringify(parseJson(existingControl?.metadataJson) ?? {}),
    };

    if (existingControl) {
      await ctx.db.patch(existingControl._id, controlDocument);
    } else {
      await ctx.db.insert("goaRunControls", controlDocument);
    }

    const agents = Array.isArray(config.agents) ? config.agents.map(asRecord) : [];
    for (const agent of agents) {
      const agentId = String(agent.agent_id ?? "");
      if (!agentId) {
        continue;
      }
      const payload = defaultAgentPayload(runId, agent);
      const existingAgent = await findAgent(ctx, runId, agentId);
      const document = {
        runId,
        agentId,
        status: String(payload.status),
        bestRating: Number(payload.best_rating_score),
        updatedAt: now,
        payloadJson: stringify(payload),
      };
      if (existingAgent) {
        await ctx.db.patch(existingAgent._id, document);
      } else {
        await ctx.db.insert("goaRunAgents", document);
      }
    }

    await ensureRunSummary(ctx, runId, {
      now,
      name: String(config.name ?? runId),
      description: String(config.description ?? ""),
      status: "pending",
      createdAt: now,
      agentCount: agents.length,
      config,
      finalScores: {},
      payouts: {},
    });
    await writeSnapshot(ctx, runId, now);
    await appendRunEvent(ctx, runId, "run.created", now, { run_id: runId });
    return { runId };
  },
});

export const startRun = mutation({
  args: { runId: v.string(), syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findRunControl(ctx, args.runId);
    if (!existing) {
      throw new Error("run not found");
    }
    await ctx.db.patch(existing._id, {
      status: "running",
      updatedAt: now,
      startedAt: existing.startedAt ?? now,
      stopRequestedAt: undefined,
      stoppedAt: undefined,
    });
    await ensureRunSummary(ctx, args.runId, {
      now,
      status: "running",
      startedAt: existing.startedAt ?? now,
      finishedAt: null,
    });
    await writeSnapshot(ctx, args.runId, now);
    await appendRunEvent(ctx, args.runId, "run.started", now, { run_id: args.runId });
    return { runId: args.runId, status: "running", startedAt: existing.startedAt ?? now };
  },
});

export const requestStop = mutation({
  args: {
    runId: v.string(),
    graceSeconds: v.optional(v.number()),
    reason: v.optional(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findRunControl(ctx, args.runId);
    if (!existing) {
      throw new Error("run not found");
    }
    const metadata = parseJson<GenericRecord>(existing.metadataJson) ?? {};
    metadata.soft_kill_grace_seconds = args.graceSeconds ?? metadata.soft_kill_grace_seconds ?? 15;
    await ctx.db.patch(existing._id, {
      status: "stopping",
      updatedAt: now,
      stopRequestedAt: now,
      stopReason: args.reason ?? "user_stop",
      metadataJson: stringify(metadata),
    });
    await ensureRunSummary(ctx, args.runId, { now, status: "stopping" });
    await appendRunEvent(ctx, args.runId, "run.stop_requested", now, {
      run_id: args.runId,
      grace_seconds: metadata.soft_kill_grace_seconds,
      reason: args.reason ?? "user_stop",
    });
    return { runId: args.runId, status: "stopping", stopRequestedAt: now };
  },
});

export const setReadOnly = mutation({
  args: { runId: v.string(), syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findRunControl(ctx, args.runId);
    if (!existing) {
      throw new Error("run not found");
    }
    const metadata = parseJson<GenericRecord>(existing.metadataJson) ?? {};
    metadata.read_only = true;
    metadata.read_only_at = now;
    await ctx.db.patch(existing._id, {
      updatedAt: now,
      metadataJson: stringify(metadata),
    });
    await appendRunEvent(ctx, args.runId, "run.read_only", now, { run_id: args.runId });
    return { runId: args.runId, readOnlyAt: now };
  },
});

export const completeRun = mutation({
  args: {
    runId: v.string(),
    finalScores: v.any(),
    payouts: v.any(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findRunControl(ctx, args.runId);
    if (!existing) {
      throw new Error("run not found");
    }
    const metadata = parseJson<GenericRecord>(existing.metadataJson) ?? {};
    metadata.read_only = true;
    metadata.read_only_at = metadata.read_only_at ?? now;
    await ctx.db.patch(existing._id, {
      status: "finished",
      updatedAt: now,
      stoppedAt: now,
      metadataJson: stringify(metadata),
    });
    await ensureRunSummary(ctx, args.runId, {
      now,
      status: "finished",
      finishedAt: now,
      finalScores: asRecord(args.finalScores),
      payouts: asRecord(args.payouts),
    });
    await writeSnapshot(ctx, args.runId, now);
    await appendRunEvent(ctx, args.runId, "run.finished", now, {
      run_id: args.runId,
      final_scores: asRecord(args.finalScores),
      payouts: asRecord(args.payouts),
    });
    return { runId: args.runId, status: "finished" };
  },
});

export const failRun = mutation({
  args: {
    runId: v.string(),
    error: v.string(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findRunControl(ctx, args.runId);
    if (!existing) {
      throw new Error("run not found");
    }
    const metadata = parseJson<GenericRecord>(existing.metadataJson) ?? {};
    metadata.read_only = true;
    metadata.read_only_at = metadata.read_only_at ?? now;
    metadata.error = args.error;
    await ctx.db.patch(existing._id, {
      status: "failed",
      updatedAt: now,
      stoppedAt: now,
      stopReason: args.error,
      metadataJson: stringify(metadata),
    });
    await ensureRunSummary(ctx, args.runId, {
      now,
      status: "failed",
      finishedAt: now,
    });
    await writeSnapshot(ctx, args.runId, now);
    await appendRunEvent(ctx, args.runId, "run.failed", now, {
      run_id: args.runId,
      error: args.error,
    });
    return { runId: args.runId, status: "failed" };
  },
});

export const registerSandbox = mutation({
  args: {
    runId: v.string(),
    sandboxId: v.string(),
    role: v.optional(v.string()),
    kind: v.optional(v.string()),
    agentId: v.optional(v.string()),
    status: v.optional(v.string()),
    metadata: v.optional(v.any()),
    heartbeatTtlSeconds: v.optional(v.number()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findSandbox(ctx, args.runId, args.sandboxId);
    const kind = args.role ?? args.kind ?? existing?.kind ?? "agent";
    const agentId = args.agentId ?? existing?.agentId ?? (kind === "agent" ? "unknown" : kind);
    const metadata = { ...(parseJson<GenericRecord>(existing?.metadataJson) ?? {}), ...asRecord(args.metadata) };
    const document = {
      runId: args.runId,
      sandboxId: args.sandboxId,
      agentId,
      kind,
      status: args.status ?? existing?.status ?? "running",
      registeredAt: existing?.registeredAt ?? now,
      lastHeartbeatAt: now,
      heartbeatTtlSeconds: args.heartbeatTtlSeconds ?? existing?.heartbeatTtlSeconds,
      metadataJson: stringify(metadata),
    };
    if (existing) {
      await ctx.db.patch(existing._id, document);
    } else {
      await ctx.db.insert("goaRunSandboxes", document);
    }

    if (kind === "agent" && agentId && agentId !== "unknown") {
      const agent = await findAgent(ctx, args.runId, agentId);
      if (agent) {
        const payload = parseJson<GenericRecord>(agent.payloadJson) ?? {};
        payload.sandbox_id = args.sandboxId;
        payload.sandbox_status = document.status;
        payload.sandbox_name = stringOrNull(metadata.name);
        if (stringOrNull(metadata.workspace)) {
          payload.workspace = stringOrNull(metadata.workspace);
        }
        payload.last_activity_at = new Date(now).toISOString();
        await ctx.db.patch(agent._id, {
          status: String(payload.status ?? agent.status),
          updatedAt: now,
          payloadJson: stringify(payload),
        });
      }
    }

    await appendRunEvent(ctx, args.runId, "sandbox.registered", now, {
      run_id: args.runId,
      sandbox_id: args.sandboxId,
      kind,
      agent_id: agentId,
    });
    return {
      runId: args.runId,
      sandboxId: args.sandboxId,
      kind,
      agentId,
      status: document.status,
      lastHeartbeatAt: now,
      metadata,
    };
  },
});

export const heartbeatSandbox = mutation({
  args: {
    runId: v.string(),
    sandboxId: v.string(),
    status: v.optional(v.string()),
    metadataPatch: v.optional(v.any()),
    heartbeatTtlSeconds: v.optional(v.number()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findSandbox(ctx, args.runId, args.sandboxId);
    if (!existing) {
      throw new Error("sandbox not found");
    }
    const metadata = {
      ...(parseJson<GenericRecord>(existing.metadataJson) ?? {}),
      ...asRecord(args.metadataPatch),
    };
    const status = args.status ?? existing.status;
    await ctx.db.patch(existing._id, {
      status,
      lastHeartbeatAt: now,
      heartbeatTtlSeconds: args.heartbeatTtlSeconds ?? existing.heartbeatTtlSeconds,
      metadataJson: stringify(metadata),
    });
    return { runId: args.runId, sandboxId: args.sandboxId, status, lastHeartbeatAt: now };
  },
});

export const finishSandbox = mutation({
  args: {
    runId: v.string(),
    sandboxId: v.string(),
    status: v.string(),
    error: v.optional(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const existing = await findSandbox(ctx, args.runId, args.sandboxId);
    if (!existing) {
      throw new Error("sandbox not found");
    }
    if (existing.status === "finished" || existing.status === "failed") {
      return { runId: args.runId, sandboxId: args.sandboxId, status: existing.status };
    }
    const metadata = parseJson<GenericRecord>(existing.metadataJson) ?? {};
    if (args.error) {
      metadata.error = args.error;
    }
    await ctx.db.patch(existing._id, {
      status: args.status,
      lastHeartbeatAt: now,
      metadataJson: stringify(metadata),
    });
    await appendRunEvent(ctx, args.runId, "sandbox.finished", now, {
      run_id: args.runId,
      sandbox_id: args.sandboxId,
      kind: existing.kind,
      agent_id: existing.agentId,
      status: args.status,
      error: args.error ?? null,
    });
    return { runId: args.runId, sandboxId: args.sandboxId, status: args.status };
  },
});

export const listSandboxes = query({
  args: { runId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    const config = parseJson<GenericRecord>(run?.configJson ?? "") ?? {};
    const now = Date.now();
    const rows = await ctx.db
      .query("goaRunSandboxes")
      .withIndex("by_run_id_last_heartbeat_at", (q) => q.eq("runId", args.runId))
      .order("desc")
      .take(256);
    return rows.map((row: any) => ({
      runId: row.runId,
      sandboxId: row.sandboxId,
      kind: row.kind,
      agentId: row.agentId,
      status: effectiveSandboxStatus(row, config, now),
      registeredAt: row.registeredAt,
      lastHeartbeatAt: row.lastHeartbeatAt,
      heartbeatTtlSeconds: sandboxHeartbeatTtlSeconds(row.kind, config, row.heartbeatTtlSeconds ?? null),
      metadata: parseJson<GenericRecord>(row.metadataJson) ?? {},
    }));
  },
});

export const getRunControl = query({
  args: { runId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    if (!run) {
      return null;
    }
    const control = await findRunControl(ctx, args.runId);
    const config = parseJson<GenericRecord>(run.configJson) ?? {};
    const metadata = parseJson<GenericRecord>(control?.metadataJson) ?? {};
    return {
      runId: run.runId,
      status: control?.status ?? run.status,
      startedAt: run.startedAt,
      finishedAt: run.finishedAt ?? null,
      config,
      metadata,
    };
  },
});

export const getAgentStepContext = query({
  args: { runId: v.string(), agentId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    if (!run) {
      return null;
    }
    const [control, sandboxes, agents, purchases] = await Promise.all([
      findRunControl(ctx, args.runId),
      ctx.db
        .query("goaRunSandboxes")
        .withIndex("by_run_id_last_heartbeat_at", (q: any) => q.eq("runId", args.runId))
        .order("desc")
        .take(16),
      ctx.db.query("goaRunAgents").withIndex("by_run_id_best_rating", (q: any) => q.eq("runId", args.runId)).order("desc").collect(),
      ctx.db.query("goaRunPurchases").withIndex("by_run_id_purchase_id", (q: any) => q.eq("runId", args.runId)).collect(),
    ]);
    const now = Date.now();
    const lifecycle = derivedRunLifecycle(run, control, sandboxes, now);
    const analytics = buildAgentAnalyticsRows(run, agents, purchases);
    const leaderboard = analytics.rows.map((row: any) => ({
      agentId: row.agent_id,
      bestRating: row.best_rating,
      projectedPayout: row.projected_payout,
      equityDelta: row.equity_delta,
      tournamentRank: row.tournament_rank,
      projectedRank: row.projected_rank,
      status: row.status,
    }));
    const self = analytics.rows.find((row: any) => String(row.agent_id) === args.agentId) ?? null;
    const agent = agents.find((row: any) => String(row.agentId) === args.agentId) ?? null;
    const payload = agent ? parseJson<GenericRecord>(agent.payloadJson) ?? {} : {};
    return {
      run: {
        runId: run.runId,
        status: lifecycle.status,
        startedAt: run.startedAt ?? null,
        finishedAt: lifecycle.finishedAt,
        createdAt: run.createdAt,
      },
      rank: self?.tournament_rank ?? Math.max(1, analytics.totalAgents),
      totalAgents: analytics.totalAgents,
      projectedRank: self?.projected_rank ?? Math.max(1, analytics.totalAgents),
      bestRating: self?.best_rating ?? agent?.bestRating ?? Number(payload.best_rating_score ?? 0),
      projectedPayout: self?.projected_payout ?? Number(payload.best_rating_score ?? 0),
      equityDelta: self?.equity_delta ?? 0,
      bestBotId: stringOrNull(payload.best_bot_id),
      leaderboard,
    };
  },
});

export const getAgentAnalytics = query({
  args: { runId: v.string(), agentId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    if (!run) {
      return null;
    }
    const [control, sandboxes, agents, purchases] = await Promise.all([
      findRunControl(ctx, args.runId),
      ctx.db
        .query("goaRunSandboxes")
        .withIndex("by_run_id_last_heartbeat_at", (q: any) => q.eq("runId", args.runId))
        .order("desc")
        .take(16),
      ctx.db.query("goaRunAgents").withIndex("by_run_id_best_rating", (q: any) => q.eq("runId", args.runId)).order("desc").collect(),
      // Cap at 5000 purchases to prevent catastrophic full-table scans
      // (additive settlement runs can produce 60K+ purchases; settlement
      // computation is approximate but still accurate for <5000)
      ctx.db.query("goaRunPurchases").withIndex("by_run_id_purchase_id", (q: any) => q.eq("runId", args.runId)).take(5000),
    ]);
    const now = Date.now();
    const lifecycle = derivedRunLifecycle(run, control, sandboxes, now);
    const analytics = buildAgentAnalyticsRows(run, agents, purchases);
    const self = analytics.rows.find((row: any) => row.agent_id === args.agentId) ?? null;
    return {
      run: {
        run_id: run.runId,
        status: lifecycle.status,
        started_at: run.startedAt ?? null,
        finished_at: lifecycle.finishedAt,
        created_at: run.createdAt,
        settlement_mode: analytics.settlementMode,
      },
      self,
      total_agents: analytics.totalAgents,
      leaderboard: analytics.rows,
    };
  },
});

export const getRunAnalysis = query({
  args: { runId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    if (!run) {
      return null;
    }
    const [control, sandboxes, agents, bots, offers, purchases, reviews, comments, games] = await Promise.all([
      findRunControl(ctx, args.runId),
      ctx.db
        .query("goaRunSandboxes")
        .withIndex("by_run_id_last_heartbeat_at", (q: any) => q.eq("runId", args.runId))
        .order("desc")
        .take(16),
      ctx.db.query("goaRunAgents").withIndex("by_run_id_best_rating", (q: any) => q.eq("runId", args.runId)).order("desc").collect(),
      ctx.db.query("goaRunBots").withIndex("by_run_id_rating", (q: any) => q.eq("runId", args.runId)).order("desc").collect(),
      ctx.db.query("goaRunOffers").withIndex("by_run_id_offer_id", (q: any) => q.eq("runId", args.runId)).take(1000),
      ctx.db.query("goaRunPurchases").withIndex("by_run_id_purchase_id", (q: any) => q.eq("runId", args.runId)).take(5000),
      ctx.db.query("goaRunReviews").withIndex("by_run_id_review_id", (q: any) => q.eq("runId", args.runId)).take(1000),
      ctx.db.query("goaRunComments").withIndex("by_run_id_comment_id", (q: any) => q.eq("runId", args.runId)).take(2000),
      ctx.db.query("goaRunGames").withIndex("by_run_id_started_at", (q: any) => q.eq("runId", args.runId)).take(1000),
    ]);
    const now = Date.now();
    const lifecycle = derivedRunLifecycle(run, control, sandboxes, now);
    return buildRunAnalysisSummary(
      run,
      lifecycle.status,
      agents,
      bots,
      offers,
      purchases,
      reviews,
      comments,
      games,
      lifecycle.finishedAt,
    );
  },
});

export const getTournamentState = query({
  args: { runId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    if (!run) {
      return null;
    }
    const [agents, bots, purchases] = await Promise.all([
      ctx.db.query("goaRunAgents").withIndex("by_run_id_best_rating", (q: any) => q.eq("runId", args.runId)).order("desc").collect(),
      ctx.db.query("goaRunBots").withIndex("by_run_id_rating", (q: any) => q.eq("runId", args.runId)).order("desc").collect(),
      ctx.db.query("goaRunPurchases").withIndex("by_run_id_purchase_id", (q: any) => q.eq("runId", args.runId)).collect(),
    ]);
    return {
      runId: run.runId,
      startedAt: run.startedAt ?? null,
      agents: agents.map((row: any) => parseJson<GenericRecord>(row.payloadJson) ?? {}),
      bots: bots
        .filter((row: any) => row.active)
        .map((row: any) => parseJson<GenericRecord>(row.payloadJson) ?? {}),
      purchases: purchases.map((row: any) => parseJson<GenericRecord>(row.payloadJson) ?? {}),
    };
  },
});

export const queryGames = query({
  args: {
    runId: v.string(),
    limit: v.optional(v.number()),
    scanLimit: v.optional(v.number()),
    order: v.optional(v.union(v.literal("asc"), v.literal("desc"))),
    agentId: v.optional(v.string()),
    botId: v.optional(v.string()),
    winnerBotId: v.optional(v.string()),
    status: v.optional(v.string()),
    reasonContains: v.optional(v.string()),
    includeActions: v.optional(v.boolean()),
  },
  handler: async (ctx, args) => {
    const limit = Math.max(1, Math.min(args.limit ?? 25, 200));
    const scanLimit = Math.max(limit, Math.min(args.scanLimit ?? Math.max(limit * 4, 64), 1000));
    const order = args.order === "asc" ? "asc" : "desc";
    const includeActions = args.includeActions ?? true;
    const rows = await ctx.db
      .query("goaRunGames")
      .withIndex("by_run_id_started_at", (q: any) => q.eq("runId", args.runId))
      .order(order)
      .take(scanLimit);
    const matches = [];
    for (const row of rows) {
      const game = hydrateGamePayload(row, includeActions);
      if (
        !gameMatchesFilters(game, {
          agentId: args.agentId ?? null,
          botId: args.botId ?? null,
          winnerBotId: args.winnerBotId ?? null,
          status: args.status ?? null,
          reasonContains: args.reasonContains ?? null,
        })
      ) {
        continue;
      }
      matches.push(game);
      if (matches.length >= limit) {
        break;
      }
    }
    return {
      filters: {
        agent_id: args.agentId ?? null,
        bot_id: args.botId ?? null,
        winner_bot_id: args.winnerBotId ?? null,
        status: args.status ?? null,
        reason_contains: args.reasonContains ?? null,
        order,
      },
      scanned: rows.length,
      returned: matches.length,
      games: matches,
    };
  },
});

export const listMarketplaceOffers = query({
  args: { runId: v.string(), limit: v.optional(v.number()) },
  handler: async (ctx, args) => {
    const limit = Math.max(1, Math.min(args.limit ?? 50, 200));
    const [offers, reviews] = await Promise.all([
      ctx.db
        .query("goaRunOffers")
        .withIndex("by_run_id_updated_at", (q: any) => q.eq("runId", args.runId))
        .order("desc")
        .take(limit),
      ctx.db.query("goaRunReviews").withIndex("by_run_id_created_at", (q: any) => q.eq("runId", args.runId)).order("desc").take(limit * 10),
    ]);
    // Fetch purchases per-offer using the indexed query instead of scanning all purchases
    const offerResults = await Promise.all(
      offers.map(async (row: any) => {
        const payload = normalizeOfferPayload(parseJson<GenericRecord>(row.payloadJson) ?? {});
        const offerId = String(payload.offer_id ?? row.offerId);
        const offerPurchases = await ctx.db
          .query("goaRunPurchases")
          .withIndex("by_run_id_offer_id", (q: any) => q.eq("runId", args.runId).eq("offerId", offerId))
          .collect();
        return {
          ...payload,
          purchases: offerPurchases
            .map((purchaseRow: any) => normalizePurchasePayload(parseJson<GenericRecord>(purchaseRow.payloadJson) ?? {})),
          reviews: reviews
            .filter((reviewRow: any) => reviewRow.offerId === offerId)
            .map((reviewRow: any) => parseJson<GenericRecord>(reviewRow.payloadJson) ?? {}),
        };
      })
    );
    return offerResults;
  },
});

export const getOfferDetails = query({
  args: { runId: v.string(), offerId: v.string() },
  handler: async (ctx, args) => {
    const offer = await findOffer(ctx, args.runId, args.offerId);
    if (!offer) {
      return null;
    }
    const [purchases, reviews] = await Promise.all([
      ctx.db.query("goaRunPurchases").withIndex("by_run_id_offer_id", (q: any) => q.eq("runId", args.runId).eq("offerId", args.offerId)).collect(),
      ctx.db.query("goaRunReviews").withIndex("by_run_id_review_id", (q: any) => q.eq("runId", args.runId)).collect(),
    ]);
    const payload = normalizeOfferPayload(parseJson<GenericRecord>(offer.payloadJson) ?? {});
    return {
      ...payload,
      purchases: purchases
        .filter((row: any) => row.offerId === args.offerId)
        .map((row: any) => normalizePurchasePayload(parseJson<GenericRecord>(row.payloadJson) ?? {})),
      reviews: reviews
        .filter((row: any) => row.offerId === args.offerId)
        .map((row: any) => parseJson<GenericRecord>(row.payloadJson) ?? {}),
    };
  },
});

export const registerBotSubmission = mutation({
  args: {
    runId: v.string(),
    agentId: v.string(),
    name: v.string(),
    description: v.optional(v.string()),
    entrypoint: v.string(),
    modulePath: v.string(),
    bundleStorageId: v.string(),
    bundleBytes: v.number(),
    filePaths: v.array(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    const now = Date.now();
    const existing = await ctx.db
      .query("goaRunBotSubmissionQueue")
      .withIndex("by_run_id_agent_id_created_at", (q) => q.eq("runId", args.runId).eq("agentId", args.agentId))
      .order("desc")
      .take(16);
    const claimed = existing.find((row: any) => row.status === "claimed");
    const submissionId = claimed?.submissionId ?? newId("sub");
    const payload = {
      submission_id: submissionId,
      agent_id: args.agentId,
      name: args.name,
      description: args.description ?? "",
      entrypoint: args.entrypoint,
      modulePath: args.modulePath,
      module_path: args.modulePath,
      bundleStorageId: args.bundleStorageId,
      bundle_storage_id: args.bundleStorageId,
      bundleBytes: args.bundleBytes,
      bundle_bytes: args.bundleBytes,
      filePaths: args.filePaths,
      file_paths: args.filePaths,
      created_at: new Date(now).toISOString(),
      status: claimed ? "claimed" : "queued",
    };
    if (claimed) {
      return {
        ...payload,
        runId: args.runId,
        submissionId,
        agentId: args.agentId,
        reusedExisting: true,
      };
    }
    await ctx.db.insert("goaRunBotSubmissionQueue", {
      runId: args.runId,
      submissionId,
      agentId: args.agentId,
      sandboxId: undefined,
      botId: undefined,
      status: "queued",
      priority: 0,
      createdAt: now,
      updatedAt: now,
      claimedAt: undefined,
      claimedBySandboxId: undefined,
      leaseExpiresAt: undefined,
      completedAt: undefined,
      error: undefined,
      payloadJson: stringify(payload),
      resultJson: undefined,
    });
    await appendRunEvent(ctx, args.runId, "bot_submission.queued", now, payload);
    return {
      ...payload,
      runId: args.runId,
      submissionId,
      agentId: args.agentId,
    };
  },
});

export const listPendingSubmissions = query({
  args: { runId: v.string(), limit: v.optional(v.number()) },
  handler: async (ctx, args) => {
    const limit = Math.max(1, Math.min(args.limit ?? 256, 2000));
    const rows = await ctx.db
      .query("goaRunBotSubmissionQueue")
      .withIndex("by_run_id_status_created_at", (q) => q.eq("runId", args.runId).eq("status", "queued"))
      .order("desc")
      .take(Math.min(limit * 8, 4000));
    const latestByAgent = new Map<string, any>();
    for (const row of rows) {
      if (!latestByAgent.has(String(row.agentId))) {
        latestByAgent.set(String(row.agentId), row);
      }
      if (latestByAgent.size >= limit) {
        break;
      }
    }
    return Array.from(latestByAgent.values()).map((row: any) => {
      const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
      return {
        ...payload,
        runId: row.runId,
        submissionId: row.submissionId,
        agentId: row.agentId,
        status: row.status,
        createdAt: row.createdAt,
      };
    });
  },
});

export const activateSubmission = mutation({
  args: {
    runId: v.string(),
    submissionId: v.string(),
    bot: v.any(),
    evictedBotId: v.optional(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const submission = await findSubmission(ctx, args.runId, args.submissionId);
    if (!submission) {
      throw new Error("submission not found");
    }
    const submissionPayload = parseJson<GenericRecord>(submission.payloadJson) ?? {};
    const bot = asRecord(args.bot);
    const botId = String(bot.bot_id ?? "");
    if (!botId) {
      throw new Error("bot_id missing");
    }
    const existingBot = await findBot(ctx, args.runId, botId);
    const botPayload = {
      ...submissionPayload,
      ...bot,
      bot_id: botId,
      active: bot.active === undefined ? true : Boolean(bot.active),
      entrypoint: String(bot.entrypoint ?? submissionPayload.entrypoint ?? ""),
      module_path: String(bot.module_path ?? submissionPayload.module_path ?? submissionPayload.modulePath ?? ""),
      modulePath: String(bot.module_path ?? submissionPayload.module_path ?? submissionPayload.modulePath ?? ""),
      bundle_storage_id:
        stringOrNull(bot.bundle_storage_id) ??
        stringOrNull(bot.bundleStorageId) ??
        stringOrNull(submissionPayload.bundle_storage_id) ??
        stringOrNull(submissionPayload.bundleStorageId),
      bundleStorageId:
        stringOrNull(bot.bundle_storage_id) ??
        stringOrNull(bot.bundleStorageId) ??
        stringOrNull(submissionPayload.bundle_storage_id) ??
        stringOrNull(submissionPayload.bundleStorageId),
    };
    const botDocument = {
      runId: args.runId,
      botId,
      agentId: String(botPayload.agent_id ?? ""),
      active: Boolean(botPayload.active),
      rating: Number(botPayload.rating_score ?? botPayload.elo ?? 0),
      name: String(botPayload.name ?? botId),
      updatedAt: now,
      payloadJson: stringify(botPayload),
    };
    if (existingBot) {
      await ctx.db.patch(existingBot._id, botDocument);
    } else {
      await ctx.db.insert("goaRunBots", botDocument);
    }

    let activeDelta = existingBot ? 0 : botDocument.active ? 1 : 0;
    let botCountDelta = existingBot ? 0 : 1;
    if (args.evictedBotId) {
      const evicted = await findBot(ctx, args.runId, args.evictedBotId);
      if (evicted && evicted.active) {
        const evictedPayload = parseJson<GenericRecord>(evicted.payloadJson) ?? {};
        evictedPayload.active = false;
        await ctx.db.patch(evicted._id, {
          active: false,
          updatedAt: now,
          payloadJson: stringify(evictedPayload),
        });
        activeDelta -= 1;
      }
    }

    const run = await findRun(ctx, args.runId);
    await ensureRunSummary(ctx, args.runId, {
      now,
      botCount: (run?.botCount ?? 0) + botCountDelta,
      activeBotCount: Math.max(0, (run?.activeBotCount ?? 0) + activeDelta),
    });

    const agent = await findAgent(ctx, args.runId, String(botPayload.agent_id ?? ""));
    if (agent) {
      const agentPayload = parseJson<GenericRecord>(agent.payloadJson) ?? {};
      const currentBest = Number(agentPayload.best_rating_score ?? 0);
      if (!agentPayload.best_bot_id || Number(botPayload.rating_score ?? 0) >= currentBest) {
        agentPayload.best_bot_id = botId;
        agentPayload.best_rating_mu = Number(botPayload.rating_mu ?? BASE_MU);
        agentPayload.best_rating_sigma = Number(botPayload.rating_sigma ?? BASE_SIGMA);
        agentPayload.best_rating_score = Number(botPayload.rating_score ?? 0);
        agentPayload.best_elo = Number(botPayload.elo ?? BASE_ELO);
      }
      agentPayload.last_activity_at = new Date(now).toISOString();
      await ctx.db.patch(agent._id, {
        bestRating: Number(agentPayload.best_rating_score ?? 0),
        updatedAt: now,
        payloadJson: stringify(agentPayload),
      });
    }

    await ctx.db.patch(submission._id, {
      botId,
      status: "completed",
      updatedAt: now,
      completedAt: now,
      resultJson: stringify({
        bot_id: botId,
        evicted_bot_id: args.evictedBotId ?? null,
      }),
    });
    await appendRunEvent(ctx, args.runId, "bot.submitted", now, {
      run_id: args.runId,
      submission_id: args.submissionId,
      agent_id: submission.agentId,
      bot_id: botId,
      evicted_bot_id: args.evictedBotId ?? null,
    });
    await writeSnapshot(ctx, args.runId, now);
    return {
      submissionId: args.submissionId,
      botId,
      evictedBotId: args.evictedBotId ?? null,
      status: "completed",
    };
  },
});

export const appendMatchResult = mutation({
  args: {
    runId: v.string(),
    match: v.any(),
    bots: v.array(v.any()),
    agents: v.array(v.any()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const result = await appendMatchResults.handler(ctx, {
      runId: args.runId,
      matches: [args.match],
      bots: args.bots,
      agents: args.agents,
      syncToken: args.syncToken,
    });
    return result.matches[0] ?? null;
  },
});

export const appendMatchResults = mutation({
  args: {
    runId: v.string(),
    matches: v.array(v.any()),
    bots: v.array(v.any()),
    agents: v.array(v.any()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await appendMatchSummaries.handler(ctx, {
      runId: args.runId,
      matches: args.matches,
      syncToken: args.syncToken,
    });
    await upsertTournamentState.handler(ctx, {
      runId: args.runId,
      bots: args.bots,
      agents: args.agents,
      syncToken: args.syncToken,
    });
    return {
      matches: args.matches.map((rawMatch: unknown) => {
        const match = asRecord(rawMatch);
        return {
          gameId: String(match.game_id ?? ""),
          status: String(match.status ?? "finished"),
        };
      }),
    };
  },
});

export const appendMatchSummaries = mutation({
  args: {
    runId: v.string(),
    matches: v.array(v.any()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const run = await findRun(ctx, args.runId);
    let newGames = 0;
    const writtenMatches: Array<{ gameId: string; status: string }> = [];

    for (const rawMatch of args.matches) {
      const match = asRecord(rawMatch);
      const gameId = String(match.game_id ?? "");
      if (!gameId) {
        continue;
      }
      const eventAt = parseTimestamp(match.finished_at, now);
      const existingGame = await findGame(ctx, args.runId, gameId);
      const gameDocument = {
        runId: args.runId,
        gameId,
        status: String(match.status ?? "finished"),
        startedAt: parseTimestamp(match.started_at, eventAt),
        finishedAt:
          match.finished_at === null || match.finished_at === undefined
            ? undefined
            : parseTimestamp(match.finished_at, eventAt),
        winnerBotId: stringOrNull(match.winner_bot_id) ?? undefined,
        tableSize: Number(match.table_size ?? 0),
        roundCount: Number(match.round_count ?? 0),
        durationSeconds:
          match.duration_seconds === null || match.duration_seconds === undefined
            ? undefined
            : Number(match.duration_seconds),
        updatedAt: eventAt,
        // Exclude full action traces from stored summaries to reduce write volume.
        // Action traces can be 10-100x the size of the summary metadata and are
        // only needed for detailed analysis, not for dashboard/analytics queries.
        payloadJson: stringify(compactMatchPayload(match, /* excludeActions */ true)),
      };
      if (existingGame) {
        await ctx.db.patch(existingGame._id, gameDocument);
      } else {
        await ctx.db.insert("goaRunGames", gameDocument);
        newGames += 1;
      }
      await appendRunEvent(ctx, args.runId, "game.finished", eventAt, {
        run_id: args.runId,
        game_id: gameId,
        winner_bot_id: stringOrNull(match.winner_bot_id),
        table_size: Number(match.table_size ?? 0),
        round_count: Number(match.round_count ?? 0),
        duration_seconds: match.duration_seconds ?? null,
      });
      writtenMatches.push({ gameId, status: String(match.status ?? "finished") });
    }
    await ensureRunSummary(ctx, args.runId, {
      now,
      gameCount: (run?.gameCount ?? 0) + newGames,
    });
    return { matches: writtenMatches };
  },
});

export const upsertTournamentState = mutation({
  args: {
    runId: v.string(),
    bots: v.array(v.any()),
    agents: v.array(v.any()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const run = await findRun(ctx, args.runId);
    let activeBotDelta = 0;

    for (const rawBot of args.bots) {
      const bot = asRecord(rawBot);
      const botId = String(bot.bot_id ?? "");
      if (!botId) {
        continue;
      }
      const existingBot = await findBot(ctx, args.runId, botId);
      const document = {
        runId: args.runId,
        botId,
        agentId: String(bot.agent_id ?? ""),
        active: Boolean(bot.active),
        rating: Number(bot.rating_score ?? bot.elo ?? 0),
        name: String(bot.name ?? botId),
        updatedAt: now,
        payloadJson: stringify(bot),
      };
      if (existingBot) {
        if (existingBot.active !== document.active) {
          activeBotDelta += document.active ? 1 : -1;
        }
        await ctx.db.patch(existingBot._id, document);
      } else {
        if (document.active) {
          activeBotDelta += 1;
        }
        await ctx.db.insert("goaRunBots", document);
      }
    }

    for (const rawAgent of args.agents) {
      const agent = asRecord(rawAgent);
      const agentId = String(agent.agent_id ?? "");
      if (!agentId) {
        continue;
      }
      const existingAgent = await findAgent(ctx, args.runId, agentId);
      const rating = Number(agent.best_rating_score ?? agent.best_elo ?? 0);
      const document = {
        runId: args.runId,
        agentId,
        status: String(agent.status ?? existingAgent?.status ?? "idle"),
        bestRating: rating,
        updatedAt: now,
        payloadJson: stringify(agent),
      };
      if (existingAgent) {
        await ctx.db.patch(existingAgent._id, document);
      } else {
        await ctx.db.insert("goaRunAgents", document);
      }
    }

    const [topAgent] = await ctx.db
      .query("goaRunAgents")
      .withIndex("by_run_id_best_rating", (q: any) => q.eq("runId", args.runId))
      .order("desc")
      .take(1);
    const bestAgentId = topAgent?.agentId ?? run?.bestAgentId ?? null;
    const bestRating = topAgent?.bestRating ?? run?.bestRating ?? run?.bestElo ?? undefined;

    await ensureRunSummary(ctx, args.runId, {
      now,
      bestAgentId,
      bestRating,
      activeBotCount: Math.max(0, (run?.activeBotCount ?? 0) + activeBotDelta),
    });
    await writeSnapshot(ctx, args.runId, now);
    return {
      bots: args.bots.length,
      agents: args.agents.length,
    };
  },
});

export const appendLogBlocks = mutation({
  args: {
    runId: v.string(),
    agentId: v.string(),
    blocks: v.array(v.any()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const written = [];
    for (const rawBlock of args.blocks) {
      const block = asRecord(rawBlock);
      const logId = String(block.block_id ?? newId("log"));
      const existing = await findRuntimeLog(ctx, args.runId, logId);
      const createdAt = parseTimestamp(block.created_at, Date.now());
      const document = {
        runId: args.runId,
        logId,
        createdAt,
        agentId: stringOrNull(block.agent_id) ?? args.agentId,
        sandboxId: stringOrNull(block.sandbox_id) ?? undefined,
        sessionId: stringOrNull(block.step_id) ?? undefined,
        blockId: stringOrNull(block.block_id) ?? logId,
        parentBlockId: stringOrNull(block.parent_block_id) ?? undefined,
        replyToLogId: stringOrNull(block.reply_to_log_id) ?? undefined,
        kind: String(block.kind ?? "text"),
        role: String(block.role ?? "assistant"),
        channel: "conversation",
        sequence: Number(block.sequence ?? 0),
        title: stringOrNull(block.title) ?? undefined,
        text: String(block.text ?? ""),
        payloadJson: stringify(compactRuntimeLogPayload(block, createdAt)),
      };
      if (existing) {
        await ctx.db.patch(existing._id, document);
      } else {
        await ctx.db.insert("goaRunRuntimeLogs", document);
      }
      written.push({ ...block, block_id: logId });
    }
    return written;
  },
});

export const touchAgentSession = mutation({
  args: {
    runId: v.string(),
    agentId: v.string(),
    status: v.string(),
    lastMessage: v.optional(v.string()),
    bestBotId: v.optional(v.string()),
    sandboxId: v.optional(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const now = Date.now();
    const agent = await findAgent(ctx, args.runId, args.agentId);
    if (!agent) {
      throw new Error("agent not found");
    }
    const payload = parseJson<GenericRecord>(agent.payloadJson) ?? {};
    if (args.sandboxId && !ownsAgentSandbox(payload, args.sandboxId)) {
      return payload;
    }
    payload.status = args.status;
    payload.last_message = args.lastMessage ?? payload.last_message ?? null;
    payload.best_bot_id = args.bestBotId ?? payload.best_bot_id ?? null;
    payload.last_activity_at = new Date(now).toISOString();
    if (args.status === "running") {
      payload.current_step_started_at = new Date(now).toISOString();
    } else {
      payload.current_step_started_at = null;
      payload.last_output_at = new Date(now).toISOString();
    }
    await ctx.db.patch(agent._id, {
      status: args.status,
      updatedAt: now,
      payloadJson: stringify(payload),
    });
    return payload;
  },
});

export const createAgentSteer = mutation({
  args: {
    runId: v.string(),
    agentId: v.string(),
    text: v.string(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const run = await findRun(ctx, args.runId);
    if (!run) {
      throw new Error("run not found");
    }
    const control = await findRunControl(ctx, args.runId);
    const status = String(control?.status ?? run.status ?? "pending");
    if (status !== "running") {
      throw new Error("run is not accepting steering");
    }
    const agent = await findAgent(ctx, args.runId, args.agentId);
    if (!agent) {
      throw new Error("agent not found");
    }
    const text = args.text.trim().slice(0, 600);
    if (!text) {
      throw new Error("steer text is empty");
    }
    const now = Date.now();
    const steerId = newId("steer");
    const payload = {
      steer_id: steerId,
      run_id: args.runId,
      agent_id: args.agentId,
      text,
      status: "pending",
      created_at: new Date(now).toISOString(),
      updated_at: new Date(now).toISOString(),
      applied_at: null,
    };
    await ctx.db.insert("goaRunAgentSteers", {
      runId: args.runId,
      steerId,
      agentId: args.agentId,
      status: "pending",
      createdAt: now,
      updatedAt: now,
      text,
      payloadJson: stringify(payload),
    });
    await appendRunEvent(ctx, args.runId, "agent.steer.created", now, payload);
    return payload;
  },
});

export const claimPendingAgentSteers = mutation({
  args: {
    runId: v.string(),
    agentId: v.string(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const rows = await ctx.db
      .query("goaRunAgentSteers")
      .withIndex("by_run_id_agent_id_status_created_at", (q: any) =>
        q.eq("runId", args.runId).eq("agentId", args.agentId).eq("status", "pending"),
      )
      .take(8);
    if (!rows.length) {
      return [];
    }
    const now = Date.now();
    const claimed = [];
    for (const row of rows) {
      const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
      payload.status = "applied";
      payload.updated_at = new Date(now).toISOString();
      payload.applied_at = new Date(now).toISOString();
      await ctx.db.patch(row._id, {
        status: "applied",
        updatedAt: now,
        appliedAt: now,
        payloadJson: stringify(payload),
      });
      claimed.push(payload);
    }
    return claimed;
  },
});

export const postComment = mutation({
  args: {
    runId: v.string(),
    authorAgentId: v.string(),
    commentatorId: v.string(),
    text: v.string(),
    parentMessageId: v.optional(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    const run = await findRun(ctx, args.runId);
    const config = parseJson<GenericRecord>(run?.configJson) ?? {};
    const commentFeed = asRecord(config.comment_feed);
    const maxChars = Number(commentFeed.max_chars ?? 280);
    const text = args.text.trim().slice(0, maxChars);
    if (!text) {
      throw new Error("comment text is empty");
    }
    const now = Date.now();
    const messageId = newId("cmt");
    const payload = {
      message_id: messageId,
      run_id: args.runId,
      author_agent_id: args.authorAgentId,
      commentator_id: args.commentatorId,
      text,
      created_at: new Date(now).toISOString(),
      sequence: now,
      parent_message_id: args.parentMessageId ?? null,
      reactions: {} as Record<string, string[]>,
    };
    await ctx.db.insert("goaRunComments", {
      runId: args.runId,
      commentId: messageId,
      authorAgentId: args.authorAgentId,
      createdAt: now,
      payloadJson: stringify(payload),
    });
    await appendRunEvent(ctx, args.runId, "comment.posted", now, payload);
    return payload;
  },
});

export const reactComment = mutation({
  args: {
    runId: v.string(),
    authorAgentId: v.string(),
    messageId: v.string(),
    emoji: v.string(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    const run = await findRun(ctx, args.runId);
    const config = parseJson<GenericRecord>(run?.configJson) ?? {};
    const allowed = Array.isArray(config.chat_allowed_reactions)
      ? config.chat_allowed_reactions.map((item) => String(item))
      : [];
    if (!allowed.includes(args.emoji)) {
      throw new Error("emoji not allowed");
    }
    const comment = await findComment(ctx, args.runId, args.messageId);
    if (!comment) {
      throw new Error("comment not found");
    }
    const now = Date.now();
    const existing = await findCommentReaction(ctx, args.runId, args.messageId, args.emoji, args.authorAgentId);
    if (existing) {
      return {
        run_id: args.runId,
        message_id: args.messageId,
        author_agent_id: args.authorAgentId,
        emoji: args.emoji,
        created_at: new Date(existing.createdAt).toISOString(),
      };
    }
    await ctx.db.insert("goaRunCommentReactions", {
      runId: args.runId,
      commentId: args.messageId,
      emoji: args.emoji,
      authorAgentId: args.authorAgentId,
      createdAt: now,
    });
    await appendRunEvent(ctx, args.runId, "comment.reacted", now, {
      run_id: args.runId,
      message_id: args.messageId,
      author_agent_id: args.authorAgentId,
      emoji: args.emoji,
    });
    return {
      run_id: args.runId,
      message_id: args.messageId,
      author_agent_id: args.authorAgentId,
      emoji: args.emoji,
      created_at: new Date(now).toISOString(),
    };
  },
});

export const listRecentMessages = query({
  args: { runId: v.string(), limit: v.optional(v.number()) },
  handler: async (ctx, args) => {
    const limit = Math.max(1, Math.min(args.limit ?? 50, 200));
    const rows = await ctx.db
      .query("goaRunComments")
      .withIndex("by_run_id_created_at", (q) => q.eq("runId", args.runId))
      .order("desc")
      .take(limit);
    return rows
      .map((row: any) => parseJson<GenericRecord>(row.payloadJson) ?? {})
      .reverse();
  },
});

export const createOffer = mutation({
  args: {
    runId: v.string(),
    sellerAgentId: v.string(),
    title: v.string(),
    description: v.optional(v.string()),
    evidence: v.optional(v.string()),
    pricePct: v.number(),
    bundleStorageId: v.string(),
    bundleBytes: v.number(),
    filePaths: v.array(v.string()),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    const now = Date.now();
    const offerId = newId("offer");
    const payload = normalizeOfferPayload({
      offer_id: offerId,
      seller_agent_id: args.sellerAgentId,
      bot_id: "",
      title: args.title,
      description: args.description ?? "",
      evidence: args.evidence ?? "",
      price_pct: args.pricePct,
      bundle_storage_id: args.bundleStorageId,
      bundle_bytes: args.bundleBytes,
      file_paths: args.filePaths,
      status: "active",
      review_count: 0,
      created_at: new Date(now).toISOString(),
      updated_at: new Date(now).toISOString(),
    });
    await ctx.db.insert("goaRunOffers", {
      runId: args.runId,
      offerId,
      agentId: args.sellerAgentId,
      botId: "",
      pricePct: args.pricePct,
      title: args.title,
      updatedAt: now,
      payloadJson: stringify(payload),
    });
    const run = await findRun(ctx, args.runId);
    await ensureRunSummary(ctx, args.runId, { now, offerCount: (run?.offerCount ?? 0) + 1 });
    await appendRunEvent(ctx, args.runId, "offer.created", now, payload);
    return payload;
  },
});

export const updateOffer = mutation({
  args: {
    runId: v.string(),
    offerId: v.string(),
    pricePct: v.number(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    const now = Date.now();
    const offer = await findOffer(ctx, args.runId, args.offerId);
    if (!offer) {
      throw new Error("offer not found");
    }
    const payload = normalizeOfferPayload(parseJson<GenericRecord>(offer.payloadJson) ?? {});
    payload.price_pct = args.pricePct;
    payload.updated_at = new Date(now).toISOString();
    await ctx.db.patch(offer._id, {
      pricePct: args.pricePct,
      updatedAt: now,
      payloadJson: stringify(payload),
    });
    await appendRunEvent(ctx, args.runId, "offer.updated", now, payload);
    return payload;
  },
});

export const purchaseOffer = mutation({
  args: {
    runId: v.string(),
    offerId: v.string(),
    buyerAgentId: v.string(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    const now = Date.now();
    const offer = await findOffer(ctx, args.runId, args.offerId);
    if (!offer) {
      throw new Error("offer not found");
    }
    const offerPayload = normalizeOfferPayload(parseJson<GenericRecord>(offer.payloadJson) ?? {});
    const purchaseId = newId("purchase");
    const payload = normalizePurchasePayload({
      purchase_id: purchaseId,
      offer_id: args.offerId,
      buyer_agent_id: args.buyerAgentId,
      seller_agent_id: offerPayload.seller_agent_id,
      price_pct: offerPayload.price_pct,
      bundle_storage_id: offerPayload.bundle_storage_id,
      file_paths: offerPayload.file_paths,
      created_at: new Date(now).toISOString(),
    });
    await ctx.db.insert("goaRunPurchases", {
      runId: args.runId,
      purchaseId,
      offerId: args.offerId,
      buyerAgentId: args.buyerAgentId,
      updatedAt: now,
      payloadJson: stringify(payload),
    });
    const run = await findRun(ctx, args.runId);
    await ensureRunSummary(ctx, args.runId, { now, purchaseCount: (run?.purchaseCount ?? 0) + 1 });
    await appendRunEvent(ctx, args.runId, "offer.purchased", now, payload);
    return payload;
  },
});

export const addReview = mutation({
  args: {
    runId: v.string(),
    offerId: v.string(),
    buyerAgentId: v.string(),
    text: v.string(),
    syncToken: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    await assertWritable(ctx, args.runId);
    // Use indexed query to check if this specific buyer purchased this specific offer
    // instead of scanning ALL purchases in the run
    const buyerPurchases = await ctx.db
      .query("goaRunPurchases")
      .withIndex("by_run_id_offer_id", (q: any) => q.eq("runId", args.runId).eq("offerId", args.offerId))
      .collect();
    const purchased = buyerPurchases.some(
      (row: any) => row.buyerAgentId === args.buyerAgentId,
    );
    if (!purchased) {
      throw new Error("only buyers may review");
    }
    const offer = await findOffer(ctx, args.runId, args.offerId);
    if (!offer) {
      throw new Error("offer not found");
    }
    const now = Date.now();
    const reviewId = newId("review");
    const payload = {
      review_id: reviewId,
      offer_id: args.offerId,
      buyer_agent_id: args.buyerAgentId,
      text: args.text.trim(),
      created_at: new Date(now).toISOString(),
    };
    await ctx.db.insert("goaRunReviews", {
      runId: args.runId,
      reviewId,
      offerId: args.offerId,
      buyerAgentId: args.buyerAgentId,
      createdAt: now,
      payloadJson: stringify(payload),
    });
    const run = await findRun(ctx, args.runId);
    await ensureRunSummary(ctx, args.runId, { now, reviewCount: (run?.reviewCount ?? 0) + 1 });
    const offerPayload = normalizeOfferPayload(parseJson<GenericRecord>(offer.payloadJson) ?? {});
    offerPayload.review_count = Number(offerPayload.review_count ?? 0) + 1;
    offerPayload.updated_at = new Date(now).toISOString();
    await ctx.db.patch(offer._id, {
      updatedAt: now,
      payloadJson: stringify(offerPayload),
    });
    await appendRunEvent(ctx, args.runId, "offer.reviewed", now, payload);
    return payload;
  },
});

export const generateUploadUrl = mutation({
  args: { syncToken: v.optional(v.string()) },
  handler: async (ctx, args) => {
    requireSyncToken(args.syncToken);
    const uploadUrl = await ctx.storage.generateUploadUrl();
    return { uploadUrl };
  },
});

export const getDownloadUrl = query({
  args: { storageId: v.string() },
  handler: async (ctx, args) => {
    const url = await ctx.storage.getUrl(args.storageId as any);
    return { url };
  },
});

export const getAgentConversation = query({
  args: {
    runId: v.string(),
    agentId: v.string(),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const limit = Math.max(1, Math.min(args.limit ?? 400, 2000));
    const rows = await ctx.db
      .query("goaRunRuntimeLogs")
      .withIndex("by_run_id_agent_id_created_at", (q) =>
        q.eq("runId", args.runId).eq("agentId", args.agentId),
      )
      .order("desc")
      .take(limit);
    return rows.reverse().map((row: any) => {
      const payload = parseJson<GenericRecord>(row.payloadJson) ?? {};
      return {
        block_id: String(row.blockId ?? row.logId),
        run_id: args.runId,
        agent_id: args.agentId,
        step_id: stringOrNull(row.sessionId),
        role: String(row.role ?? "assistant"),
        kind: String(row.kind ?? "text"),
        title: String(row.title ?? "Response"),
        text: String(row.text ?? ""),
        collapsed: Boolean(payload.collapsed),
        streaming: Boolean(payload.streaming),
        created_at: new Date(row.createdAt).toISOString(),
        updated_at: String(payload.updated_at ?? new Date(row.createdAt).toISOString()),
      };
    });
  },
});

export const getTournamentSnapshot = query({
  args: { runId: v.string() },
  handler: async (ctx, args) => {
    const run = await findRun(ctx, args.runId);
    const control = await findRunControl(ctx, args.runId);
    const now = Date.now();
    const config = parseJson<GenericRecord>(run?.configJson ?? "") ?? {};
    const tournamentSandbox = await ctx.db
      .query("goaRunSandboxes")
      .withIndex("by_run_id_last_heartbeat_at", (q) => q.eq("runId", args.runId))
      .order("desc")
      .take(16);
    const tournament = tournamentSandbox.find((row: any) => row.kind === "tournament") ?? null;
    const queued = await ctx.db
      .query("goaRunBotSubmissionQueue")
      .withIndex("by_run_id_status_created_at", (q) => q.eq("runId", args.runId).eq("status", "queued"))
      .order("asc")
      .take(256);
    return {
      runId: args.runId,
      status: run ? derivedRunLifecycle(run, control, tournamentSandbox, now).status : control?.status ?? "unknown",
      gameCount: run?.gameCount ?? 0,
      botCount: run?.botCount ?? 0,
      activeBotCount: run?.activeBotCount ?? 0,
      queuedSubmissions: queued.length,
      tournament: tournament
        ? {
            sandboxId: tournament.sandboxId,
            status: effectiveSandboxStatus(tournament, config, now),
            lastHeartbeatAt: tournament.lastHeartbeatAt,
            metadata: parseJson<GenericRecord>(tournament.metadataJson) ?? {},
          }
        : null,
      control: control
        ? {
            status: control.status,
            startedAt: control.startedAt ?? null,
            stopRequestedAt: control.stopRequestedAt ?? null,
            stoppedAt: control.stoppedAt ?? null,
            metadata: parseJson<GenericRecord>(control.metadataJson) ?? {},
          }
        : null,
    };
  },
});
