import type { RunConfig } from "./types";

function item(label: string, value: string | number | boolean | null | undefined) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  return (
    <div key={label} className="config-bar__item">
      <dt>{label}</dt>
      <dd>{String(value)}</dd>
    </div>
  );
}

export function ConfigSummary({ config }: { config: RunConfig }) {
  const ratingSpread = config.rating?.matchmaking_spread ?? config.elo_spread;
  const timeBank = config.game?.game_time_bank_seconds ?? config.game_time_bank_seconds;
  const increment = config.game?.action_increment_seconds ?? config.action_increment_seconds;
  const players = config.game?.players_per_match;
  const hands = config.game?.max_rounds_per_match;
  const ratingSystem = config.rating?.system;
  const commentFeed = config.comment_feed?.enabled
    ? config.comment_feed.interval_seconds !== undefined
      ? `Every ${config.comment_feed.interval_seconds}s`
      : `Every ${config.comment_feed.interval_minutes ?? "?"} min`
    : "Off";

  return (
    <dl className="config-bar">
      {item("Duration", `${config.duration_minutes} min`)}
      {item("Rating", ratingSystem ?? "elo")}
      {item("Rating Spread", ratingSpread)}
      {item("Players / Match", players)}
      {item("Match Format", config.game?.match_format)}
      {item("Hand Cap", hands ?? "No cap")}
      {item("Settlement", config.settlement_mode)}
      {item("Matches", config.concurrent_matches)}
      {item("Max Bots", `${config.max_active_bots_per_agent}/agent`)}
      {item("Time Bank", timeBank !== undefined ? `${timeBank}s` : null)}
      {item("Increment", increment !== undefined ? `${increment}s` : null)}
      {item("Comment Feed", commentFeed)}
      {item("Agents", config.agents?.length ?? 0)}
    </dl>
  );
}
