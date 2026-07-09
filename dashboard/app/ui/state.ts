import type {
  AgentState,
  BotState,
  Game,
  Offer,
  Purchase,
  RawRunState,
  Review,
  RunState,
  RunStateCollection,
} from "./types";

function toRecord<T extends Record<string, unknown>>(
  collection: RunStateCollection<T> | undefined,
  idField: string,
): Record<string, T> {
  if (!collection) {
    return {};
  }
  if (!Array.isArray(collection)) {
    return collection;
  }
  return Object.fromEntries(
    collection
      .filter((item) => item && typeof item === "object")
      .map((item, index) => [String(item[idField] ?? `${idField}-${index}`), item]),
  );
}

export function normalizeRunState(state: RawRunState | undefined | null): RunState {
  return {
    agents: toRecord<AgentState>(state?.agents, "agent_id"),
    bots: toRecord<BotState>(state?.bots, "bot_id"),
    games: toRecord<Game>(state?.games, "game_id"),
    offers: toRecord<Offer>(state?.offers, "offer_id"),
    purchases: toRecord<Purchase>(state?.purchases, "purchase_id"),
    reviews: toRecord<Review>(state?.reviews, "review_id"),
  };
}
