import type { LeaderboardAgent, Purchase, RunConfig } from "./types";

export type SettlementRow = {
  agentId: string;
  baseScore: number;
  payout: number;
  delta: number;
  status: string;
  bestBotId: string | null;
};

export function formatSignedDelta(value: number): string {
  const rounded = value.toFixed(1);
  return value >= 0 ? `(+${rounded})` : `(${rounded})`;
}

export function displayDelta(value: number): string | null {
  return Math.abs(value) >= 0.05 ? formatSignedDelta(value) : null;
}

export function buildSettlementRows(
  agents: LeaderboardAgent[],
  purchases: Record<string, Purchase> | Purchase[] | undefined,
  settlementMode: RunConfig["settlement_mode"] | undefined,
  finalScores?: Record<string, number> | null,
  payouts?: Record<string, number> | null,
): SettlementRow[] {
  const baseScores = Object.fromEntries(
    agents.map((agent) => [agent.agentId, Number(finalScores?.[agent.agentId] ?? agent.bestRating ?? 0)]),
  );
  const projected = Object.fromEntries(
    agents.map((agent) => [agent.agentId, Number(payouts?.[agent.agentId] ?? baseScores[agent.agentId] ?? 0)]),
  );

  if (!payouts || Object.keys(payouts).length === 0) {
    const purchaseList = Array.isArray(purchases) ? purchases : Object.values(purchases ?? {});
    for (const purchase of purchaseList) {
      const buyer = purchase.buyer_agent_id;
      const seller = purchase.seller_agent_id;
      if (!buyer || !seller) {
        continue;
      }
      const transfer = (baseScores[buyer] ?? 0) * (Number(purchase.price_pct ?? 0) / 100);
      if (settlementMode === "net") {
        projected[buyer] = (projected[buyer] ?? baseScores[buyer] ?? 0) - transfer;
      }
      projected[seller] = (projected[seller] ?? baseScores[seller] ?? 0) + transfer;
    }
  }

  return agents
    .map((agent) => {
      const baseScore = baseScores[agent.agentId] ?? 0;
      const payout = projected[agent.agentId] ?? baseScore;
      return {
        agentId: agent.agentId,
        baseScore,
        payout,
        delta: payout - baseScore,
        status: agent.status,
        bestBotId: agent.bestBotId,
      };
    })
    .sort((left, right) => right.payout - left.payout || right.baseScore - left.baseScore || left.agentId.localeCompare(right.agentId));
}
