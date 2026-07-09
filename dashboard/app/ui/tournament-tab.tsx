"use client";

import { useMemo, useState } from "react";
import type { Game, GameParticipant, LeaderboardAgent, LeaderboardBot, RunConfig, RunState } from "./types";
import { buildSettlementRows, displayDelta } from "./settlement";

const PAGE_SIZE = 24;
const RECENT_GAME_LIMIT = 16;

function fmtRating(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toFixed(1);
}

function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  return `${s.toFixed(1)}s`;
}

function fmtChips(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return Math.round(n).toString();
}

function rankBadge(rank: number): string {
  if (rank === 1) return "🥇";
  if (rank === 2) return "🥈";
  if (rank === 3) return "🥉";
  return `#${rank}`;
}

function average(values: number[]): number | null {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function TournamentTab({
  agents,
  bots,
  state,
  totalGameCount,
  finished,
  settlementMode,
  finalScores,
  payouts,
}: {
  agents: LeaderboardAgent[];
  bots: LeaderboardBot[];
  state: RunState;
  totalGameCount: number;
  finished?: boolean;
  settlementMode?: RunConfig["settlement_mode"];
  finalScores?: Record<string, number> | null;
  payouts?: Record<string, number> | null;
}) {
  const botMap = (state.bots ?? {}) as Record<string, { agent_id: string; name?: string }>;
  const participantsForGame = (game: Game): GameParticipant[] => {
    if (game.participants?.length) {
      return game.participants;
    }
    const participants: GameParticipant[] = [];
    if (game.bot_a_id) {
      participants.push({
        bot_id: game.bot_a_id,
        agent_id: game.agent_a_id ?? botMap[game.bot_a_id]?.agent_id ?? "?",
        seat: 0,
        placement: game.winner_bot_id === game.bot_a_id ? 1 : 2,
        ending_chips: 0,
      });
    }
    if (game.bot_b_id) {
      participants.push({
        bot_id: game.bot_b_id,
        agent_id: game.agent_b_id ?? botMap[game.bot_b_id]?.agent_id ?? "?",
        seat: 1,
        placement: game.winner_bot_id === game.bot_b_id ? 1 : 2,
        ending_chips: 0,
      });
    }
    return participants;
  };

  const allGames = useMemo(
    () =>
      Object.values(state.games ?? {})
        .sort((a, b) => {
          const ta = typeof a.started_at === "number" ? a.started_at : 0;
          const tb = typeof b.started_at === "number" ? b.started_at : 0;
          return tb - ta;
        }),
    [state.games],
  );

  const allAgentIds = useMemo(() => ["All", ...agents.map((a) => a.agentId)], [agents]);
  const allBotIds = useMemo(() => ["All", ...bots.map((b) => b.botId)], [bots]);
  const statusOptions = ["All", "finished", "forfeit", "running"];

  const [filterAgent, setFilterAgent] = useState("All");
  const [filterBot, setFilterBot] = useState("All");
  const [filterStatus, setFilterStatus] = useState("All");
  const [showCount, setShowCount] = useState(PAGE_SIZE);

  const filtered = useMemo(() => {
    let games = allGames;
    if (filterStatus !== "All") {
      games = games.filter((g) => g.status === filterStatus);
    }
    if (filterAgent !== "All") {
      games = games.filter((g) => participantsForGame(g).some((participant) => participant.agent_id === filterAgent));
    }
    if (filterBot !== "All") {
      games = games.filter((g) => participantsForGame(g).some((participant) => participant.bot_id === filterBot));
    }
    return games;
  }, [allGames, filterAgent, filterBot, filterStatus]);

  const runningGames = useMemo(
    () => allGames.filter((game) => game.status === "running").length,
    [allGames],
  );
  const avgDuration = useMemo(
    () =>
      average(
        allGames
          .map((game) => game.duration_seconds)
          .filter((value): value is number => typeof value === "number" && Number.isFinite(value)),
      ),
    [allGames],
  );
  const avgTableSize = useMemo(
    () =>
      average(
        allGames
          .map((game) => game.table_size ?? participantsForGame(game).length)
          .filter((value): value is number => typeof value === "number" && value > 0),
      ),
    [allGames],
  );
  const settlementRows = useMemo(
    () => buildSettlementRows(agents, state.purchases, settlementMode, finalScores, payouts),
    [agents, finalScores, payouts, settlementMode, state.purchases],
  );
  const recentGamesLabel = totalGameCount > allGames.length ? `last ${RECENT_GAME_LIMIT.toLocaleString()} loaded` : "all loaded";

  function participantLabel(participant: GameParticipant): string {
    const name = botMap[participant.bot_id]?.name;
    return name || participant.bot_id;
  }

  return (
    <div className="tournament">
      <div className="tournament__overview">
        <div className="tournament__stat-card panel">
          <span className="tournament__stat-label">Live Tables</span>
          <strong className="tournament__stat-value">{runningGames}</strong>
          <span className="tournament__stat-sub">matches currently in flight</span>
        </div>
        <div className="tournament__stat-card panel">
          <span className="tournament__stat-label">Finished Games</span>
          <strong className="tournament__stat-value">{totalGameCount}</strong>
          <span className="tournament__stat-sub">{recentGamesLabel}</span>
        </div>
        <div className="tournament__stat-card panel">
          <span className="tournament__stat-label">Average Runtime</span>
          <strong className="tournament__stat-value">{fmtDuration(avgDuration)}</strong>
          <span className="tournament__stat-sub">full game wall-clock duration</span>
        </div>
        <div className="tournament__stat-card panel">
          <span className="tournament__stat-label">Average Table Size</span>
          <strong className="tournament__stat-value">
            {avgTableSize === null ? "—" : avgTableSize.toFixed(1)}
          </strong>
          <span className="tournament__stat-sub">players per match</span>
        </div>
      </div>

      <div className="tournament__boards">
        <section className="tournament__board panel">
          <div className="tournament__board-head">
            <div>
              <h3>{finished ? "Final Agent Standings" : "Agent Standings"}</h3>
              <p>{finished ? "Ranked by payout after marketplace settlement." : "Ranked by projected payout including marketplace equity."}</p>
            </div>
            <span className="tournament__board-count">{agents.length} tracked</span>
          </div>
          <div className="tournament__leader-list">
            {settlementRows.map((agent, index) => (
                  <div key={agent.agentId} className="tournament__leader-row" data-rank={index + 1}>
                    <div className="tournament__leader-rank">{rankBadge(index + 1)}</div>
                    <div className="tournament__leader-body">
                      <strong>{agent.agentId}</strong>
                      <span>
                        {agent.status} · best bot {agent.bestBotId ?? "—"}
                      </span>
                    </div>
                    <div className="tournament__leader-value">
                      {fmtRating(agent.payout)}
                      {displayDelta(agent.delta) ? (
                        <span className="tournament__leader-delta">{displayDelta(agent.delta)}</span>
                      ) : null}
                    </div>
                  </div>
                ))}
          </div>
        </section>

        <section className="tournament__board panel">
          <div className="tournament__board-head">
            <div>
              <h3>Bot Ladder</h3>
              <p>Current bot pool with active and retired revisions.</p>
            </div>
            <span className="tournament__board-count">{bots.length} bots</span>
          </div>
          <div className="tournament__leader-list">
            {bots.map((bot, index) => (
              <div key={bot.botId} className="tournament__leader-row" data-rank={index + 1}>
                <div className="tournament__leader-rank">{rankBadge(index + 1)}</div>
                <div className="tournament__leader-body">
                  <strong>{bot.name || bot.botId}</strong>
                  <span>
                    {bot.agentId} · {bot.active ? "active" : "retired"}
                  </span>
                </div>
                <div className="tournament__leader-value">{fmtRating(bot.rating)}</div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="tournament__games panel">
        <div className="tournament__games-head">
          <div>
            <h3>Recent Match Feed</h3>
            <p>Recent tables, winners, durations, and hand counts. Older games are summarized in the total above.</p>
          </div>
          <span className="tournament__count">{filtered.length} recent games</span>
        </div>

        <div className="tournament__filters">
          <label>
            Agent
            <select value={filterAgent} onChange={(e) => { setFilterAgent(e.target.value); setShowCount(PAGE_SIZE); }}>
              {allAgentIds.map((id) => <option key={id}>{id}</option>)}
            </select>
          </label>
          <label>
            Bot
            <select value={filterBot} onChange={(e) => { setFilterBot(e.target.value); setShowCount(PAGE_SIZE); }}>
              {allBotIds.map((id) => <option key={id}>{id}</option>)}
            </select>
          </label>
          <label>
            Status
            <select value={filterStatus} onChange={(e) => { setFilterStatus(e.target.value); setShowCount(PAGE_SIZE); }}>
              {statusOptions.map((status) => <option key={status}>{status}</option>)}
            </select>
          </label>
        </div>

        {filtered.length ? (
          <div className="tournament__match-grid">
            {filtered.slice(0, showCount).map((game) => {
              const participants = participantsForGame(game).sort((left, right) => left.placement - right.placement);
              return (
                <article key={game.game_id} className="match-card" data-status={game.status}>
                  <div className="match-card__head">
                    <span className={`game-status game-status--${game.status}`}>{game.status}</span>
                    <span className="match-card__meta">
                      Table {game.table_size ?? participants.length} · {game.round_count ?? "—"} hands · {fmtDuration(game.duration_seconds)}
                    </span>
                  </div>
                  <div className="match-card__players">
                    {participants.map((participant) => (
                      <div
                        key={`${game.game_id}-${participant.bot_id}`}
                        className="match-card__player"
                        data-winner={String(participant.placement === 1)}
                      >
                        <div className="match-card__placement">{rankBadge(participant.placement)}</div>
                        <div className="match-card__identity">
                          <strong>{participantLabel(participant)}</strong>
                          <span>
                            {participant.agent_id} · {fmtChips(participant.ending_chips)} chips
                          </span>
                        </div>
                        {participant.placement === 1 && <span className="match-card__winner">Winner</span>}
                      </div>
                    ))}
                  </div>
                  <div className="match-card__footer">
                    <span>{game.reason ?? "No special finish note."}</span>
                    {game.max_rounds_reached ? <span>Reached hand cap</span> : null}
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <div className="empty">No games match the current filters.</div>
        )}

        {filtered.length > showCount && (
          <button
            type="button"
            className="btn btn--block"
            onClick={() => setShowCount((count) => count + PAGE_SIZE)}
          >
            Show more ({filtered.length - showCount} remaining)
          </button>
        )}
      </section>
    </div>
  );
}
