import "server-only";
import { ConvexHttpClient } from "convex/browser";

/**
 * Direct-to-Convex backend support.
 *
 * Allows the dashboard to surface runs from a Convex deployment without going
 * through a Modal API server. Useful for accessing legacy runs whose Modal
 * deployment was rebuilt with a different API_TOKEN (we lose the token but
 * still have the Convex URL).
 *
 * A backend URL is treated as Convex-direct if its host ends in
 * `convex.cloud`. Configured via the same API_URLS env var as Modal backends.
 */

export function isConvexUrl(url: string): boolean {
  try {
    const host = new URL(url).host;
    return host.endsWith(".convex.cloud") || host.endsWith(".convex.site");
  } catch {
    return false;
  }
}

type ConvexRunSummary = {
  runId: string;
  name?: string;
  description?: string;
  status: string;
  createdAt?: number;
  startedAt?: number | null;
  finishedAt?: number | null;
  updatedAt?: number;
  agentCount?: number;
  botCount?: number;
  activeBotCount?: number;
  gameCount?: number;
  offerCount?: number;
  purchaseCount?: number;
  reviewCount?: number;
  bestAgentId?: string | null;
  bestRating?: number | null;
  config?: Record<string, unknown> | null;
  finalScores?: Record<string, number> | null;
  payouts?: Record<string, number> | null;
};

/**
 * Convert a Convex listRuns row to the snake_case shape the dashboard
 * frontend expects (matching the FastAPI /runs response).
 */
function convertConvexRunSummary(
  run: ConvexRunSummary,
  backendUrl: string,
): Record<string, unknown> {
  return {
    run_id: run.runId,
    name: run.name ?? null,
    description: run.description ?? "",
    status: run.status,
    created_at: run.createdAt ?? null,
    started_at: run.startedAt ?? null,
    finished_at: run.finishedAt ?? null,
    updated_at: run.updatedAt ?? null,
    agent_count: run.agentCount ?? 0,
    bot_count: run.botCount ?? 0,
    game_count: run.gameCount ?? 0,
    offer_count: run.offerCount ?? 0,
    purchase_count: run.purchaseCount ?? 0,
    review_count: run.reviewCount ?? 0,
    best_rating: run.bestRating ?? null,
    config: run.config ?? null,
    final_scores: run.finalScores ?? null,
    payouts: run.payouts ?? null,
    // Empty placeholders so legacy fallback in toRunListItem returns 0 not NaN
    agents: {},
    bots: {},
    games: {},
    offers: {},
    _backend: backendUrl,
  };
}

/**
 * Fetch the list of runs directly from a Convex deployment.
 * Returns rows in the same shape as the Modal /runs endpoint, so they can
 * be merged transparently into fetchAllBackends results.
 */
export async function fetchConvexRuns(
  backendUrl: string,
): Promise<Record<string, unknown>[]> {
  const client = new ConvexHttpClient(backendUrl);
  // runs:listRuns returns at most 100 most-recent runs
  const runs = (await client.query("runs:listRuns" as never, {} as never)) as
    | ConvexRunSummary[]
    | null;
  if (!Array.isArray(runs)) return [];
  return runs.map((r) => convertConvexRunSummary(r, backendUrl));
}

/**
 * Fetch a single run's dashboard payload directly from Convex.
 * Returns the same shape as `GET /runs/{id}/dashboard` from FastAPI so the
 * frontend can render it without changes.
 *
 * Tries the full query first, falls back to the sampled variant if the run
 * is too large (>16MB read limit).
 */
export async function fetchConvexRunDashboard(
  backendUrl: string,
  runId: string,
  opts: { sampleFullHistory?: boolean; sampleSize?: number; eventLimit?: number } = {},
): Promise<Response> {
  const client = new ConvexHttpClient(backendUrl);
  const sampleSize = opts.sampleSize ?? 180;
  const eventLimit = opts.eventLimit ?? 200;

  let payload: unknown = null;
  try {
    if (opts.sampleFullHistory) {
      payload = await client.query("runs:getRunDashboardSampled" as never, {
        runId,
        sampleSize,
        eventLimit,
      } as never);
    } else {
      payload = await client.query("runs:getRunDashboard" as never, {
        runId,
      } as never);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    // Fall back to sampled version on byte-limit errors
    if (msg.includes("Too many bytes") || msg.includes("byte")) {
      try {
        payload = await client.query("runs:getRunDashboardSampled" as never, {
          runId,
          sampleSize,
          eventLimit,
        } as never);
      } catch (err2) {
        return Response.json(
          { detail: `Convex query failed: ${err2 instanceof Error ? err2.message : String(err2)}` },
          { status: 502 },
        );
      }
    } else {
      return Response.json({ detail: `Convex query failed: ${msg}` }, { status: 502 });
    }
  }

  if (!payload) {
    return Response.json({ detail: "run not found" }, { status: 404 });
  }
  return Response.json(payload);
}
