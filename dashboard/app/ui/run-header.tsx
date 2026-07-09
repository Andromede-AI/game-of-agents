"use client";

import { useMemo, useState } from "react";
import type { LeaderboardAgent, RunConfig, RunState } from "./types";
import { ConfigSummary } from "./config-summary";
import { effectiveRunStatus, isActiveRunStatus } from "./run-status";
import { buildSettlementRows, displayDelta } from "./settlement";

export function RunHeader({
  name,
  status,
  finishedAt,
  config,
  state,
  agents,
  agentCount,
  botCount,
  gameCount,
  finalScores,
  payouts,
  stopPending,
  deletePending,
  actionError,
  onStop,
  onDuplicate,
  deleteError,
  onDelete,
}: {
  name: string;
  status: string;
  finishedAt?: number | null;
  config: RunConfig;
  state: RunState;
  agents: LeaderboardAgent[];
  agentCount: number;
  botCount: number;
  gameCount: number;
  finalScores?: Record<string, number> | null;
  payouts?: Record<string, number> | null;
  stopPending: boolean;
  deletePending: boolean;
  actionError: string | null;
  onStop: () => void;
  onDuplicate: () => void;
  deleteError: string | null;
  onDelete: () => void;
}) {
  const [showConfig, setShowConfig] = useState(false);
  const displayStatus = effectiveRunStatus(status, finishedAt);
  const canStop = isActiveRunStatus(displayStatus);
  const winner = useMemo(() => {
    return buildSettlementRows(
      agents,
      state.purchases,
      config.settlement_mode,
      finalScores,
      payouts,
    )[0] ?? null;
  }, [agents, config.settlement_mode, finalScores, payouts, state.purchases]);

  return (
    <>
      <div className="channel-info">
        <h2 className="channel-info__name">{name}</h2>
        <span className="status-pill">{displayStatus}</span>
        <div className="channel-info__stats">
          <span className="channel-info__stat">{agentCount} agents</span>
          <span className="channel-info__stat">{botCount} bots</span>
          <span className="channel-info__stat">{gameCount} games</span>
          {winner && (
            <span className="channel-info__stat winner-label">
              {displayStatus === "finished" ? "Winner" : "Projected Winner"}: {winner.agentId} {winner.payout.toFixed(1)}
              {displayDelta(winner.delta) ? ` ${displayDelta(winner.delta)}` : ""}
            </span>
          )}
        </div>
        <div className="channel-info__actions">
          <button
            type="button"
            className="btn btn--sm"
            onClick={onDuplicate}
          >
            Duplicate Run
          </button>
          {canStop && (
            <button
              type="button"
              className="btn btn--sm"
              onClick={onStop}
              disabled={stopPending || displayStatus === "stopping"}
            >
              {stopPending || displayStatus === "stopping" ? "Cancelling..." : "Cancel Run"}
            </button>
          )}
          <button
            type="button"
            className="btn btn--sm btn--danger"
            onClick={onDelete}
            disabled={deletePending || canStop}
          >
            {deletePending ? "Deleting..." : "Delete Run"}
          </button>
          <button
            type="button"
            className="channel-info__toggle"
            onClick={() => setShowConfig((v) => !v)}
          >
            Config {showConfig ? "▾" : "▸"}
          </button>
        </div>
      </div>
      {actionError && <div className="channel-info__error">{actionError}</div>}
      {deleteError && <div className="channel-info__error">{deleteError}</div>}
      <div className="config-bar-wrap" data-open={String(showConfig)}>
        <ConfigSummary config={config} />
      </div>
    </>
  );
}
