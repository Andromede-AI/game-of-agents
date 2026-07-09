import { useMemo } from "react";
import type { Snapshot } from "./types";

export type RatingEntity = { id: string; label: string; agentId?: string };
export type RatingDataPoint = { time: number; [entityId: string]: number | null };
const MAX_POINTS = 320;

function downsample(points: RatingDataPoint[]): RatingDataPoint[] {
  if (points.length <= MAX_POINTS) {
    return points;
  }
  const indexes = new Set<number>([0, points.length - 1]);
  const stride = (points.length - 1) / (MAX_POINTS - 1);
  for (let slot = 1; slot < MAX_POINTS - 1; slot += 1) {
    indexes.add(Math.round(slot * stride));
  }
  return Array.from(indexes)
    .sort((left, right) => left - right)
    .map((index) => points[index]);
}

export function useRatingSeries(
  snapshots: Snapshot[],
  mode: "agents" | "bots",
): { data: RatingDataPoint[]; entities: RatingEntity[] } {
  return useMemo(() => {
    if (!snapshots.length) return { data: [], entities: [] };

    const entityMap = new Map<string, RatingEntity>();
    const retiredBots = new Set<string>();

    const data: RatingDataPoint[] = snapshots.map((snap) => {
      const point: RatingDataPoint = { time: snap.createdAt };
      const list =
        mode === "agents"
          ? snap.payload?.leaderboard?.agents ?? []
          : snap.payload?.leaderboard?.bots ?? [];

      for (const entry of list) {
        if (mode === "agents") {
          const a = entry as { agentId: string; bestRating: number };
          point[a.agentId] = a.bestRating;
          if (!entityMap.has(a.agentId)) {
            entityMap.set(a.agentId, { id: a.agentId, label: a.agentId });
          }
        } else {
          const b = entry as { botId: string; agentId: string; rating: number; name: string; active?: boolean };
          if (retiredBots.has(b.botId)) {
            continue;
          }
          point[b.botId] = b.rating;
          if (!entityMap.has(b.botId)) {
            entityMap.set(b.botId, { id: b.botId, label: b.name || b.botId, agentId: b.agentId });
          }
          if (b.active === false) {
            retiredBots.add(b.botId);
          }
        }
      }
      return point;
    });

    const entities = Array.from(entityMap.values()).sort((left, right) => {
      const leftGroup = left.agentId ?? left.id;
      const rightGroup = right.agentId ?? right.id;
      if (leftGroup !== rightGroup) {
        return leftGroup.localeCompare(rightGroup);
      }
      return left.label.localeCompare(right.label);
    });

    return { data: downsample(data), entities };
  }, [snapshots, mode]);
}
