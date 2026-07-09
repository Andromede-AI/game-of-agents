"use client";

import { useMemo, useState } from "react";

import type { RunAnalysisSummary, RunListItem } from "./types";
import { effectiveRunStatus } from "./run-status";

function initials(name?: string | null) {
  const label = typeof name === "string" && name.trim() ? name.trim() : "?";
  return label.slice(0, 2).toUpperCase();
}

function statusClass(status: string) {
  if (status === "running") return "run-icon__status run-icon__status--running";
  if (status === "finished") return "run-icon__status run-icon__status--finished";
  return "run-icon__status run-icon__status--pending";
}

function conditionLabel(name: string): string {
  const normalized = name.toLowerCase().replace(/^exp-/, "");
  return (
    normalized
      .replace(/^full-/, "")
      .replace(/^baseline-/, "base-")
      .replace(/^pilot-?/, "pilot")
      .replace(/-?tournament-?/, "")
      || normalized
  );
}

function groupByCondition(runs: RunListItem[]): Map<string, RunListItem[]> {
  const groups = new Map<string, RunListItem[]>();
  for (const run of runs) {
    const key = conditionLabel(run.name);
    const list = groups.get(key) ?? [];
    list.push(run);
    groups.set(key, list.sort((left, right) => right.createdAt - left.createdAt));
  }
  return groups;
}

function average(values: number[]) {
  if (!values.length) {
    return null;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function aggregateGroupStatus(runs: RunListItem[]) {
  const statuses = runs.map((run) => effectiveRunStatus(run.status, run.finishedAt));
  if (statuses.some((status) => status === "running")) {
    return "running";
  }
  if (statuses.every((status) => status === "finished")) {
    return "finished";
  }
  return statuses[0] ?? "pending";
}

function groupStatusClass(status: string) {
  if (status === "running") return "sidebar__group-status sidebar__group-status--running";
  if (status === "finished") return "sidebar__group-status sidebar__group-status--finished";
  return "sidebar__group-status sidebar__group-status--pending";
}

function groupTooltip(condition: string, runs: RunListItem[], analysesById: Record<string, RunAnalysisSummary | undefined>) {
  const giniValues = runs
    .map((run) => analysesById[run.runId]?.giniCoefficient)
    .filter((value): value is number => typeof value === "number");
  const avgGini = average(giniValues);
  const totalGames = runs.reduce((sum, run) => sum + (run.gameCount ?? 0), 0);
  const totalOffers = runs.reduce(
    (sum, run) => sum + (analysesById[run.runId]?.marketplace.totalOffers ?? run.offerCount ?? 0),
    0,
  );
  const status = aggregateGroupStatus(runs);
  return {
    condition,
    totalGames,
    totalOffers,
    avgGini,
    status,
  };
}

function CompareToggle({
  selected,
  onClick,
}: {
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="sidebar__compare-toggle"
      data-selected={String(selected)}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      title={selected ? "Remove from comparison" : "Add to comparison"}
    >
      {selected ? "✓" : "+"}
    </button>
  );
}

function RunButton({
  run,
  selectedRunId,
  selectedCompare,
  grouped,
  onSelect,
  onToggleCompare,
}: {
  run: RunListItem;
  selectedRunId: string | null;
  selectedCompare: boolean;
  grouped?: boolean;
  onSelect: (id: string) => void;
  onToggleCompare: (id: string) => void;
}) {
  const status = effectiveRunStatus(run.status, run.finishedAt);

  return (
    <div className={`run-icon-wrap${grouped ? " run-icon-wrap--grouped" : ""}`}>
      <CompareToggle selected={selectedCompare} onClick={() => onToggleCompare(run.runId)} />
      <button
        type="button"
        className={`run-icon${grouped ? " run-icon--grouped" : ""}`}
        data-active={String(run.runId === selectedRunId)}
        onClick={() => onSelect(run.runId)}
      >
        {initials(run.name)}
        <span className={statusClass(status)} />
      </button>
      <div className="run-tooltip">
        <div className="run-tooltip__name">{run.name || run.runId}</div>
        <div className="run-tooltip__meta">
          {status} · {run.agentCount ?? 0} agents · {run.gameCount ?? 0} games · {run.offerCount ?? 0} offers
        </div>
        <div className="run-tooltip__id">{run.runId}</div>
      </div>
    </div>
  );
}

export function RunSidebar({
  runs,
  selectedRunId,
  selectedCompareIds,
  analysesById,
  onSelect,
  onToggleCompare,
  onSelectGroup,
  onWarmGroup,
  onNewRun,
}: {
  runs: RunListItem[] | undefined;
  selectedRunId: string | null;
  selectedCompareIds: string[];
  analysesById: Record<string, RunAnalysisSummary | undefined>;
  onSelect: (id: string) => void;
  onToggleCompare: (id: string) => void;
  onSelectGroup: (runIds: string[]) => void;
  onWarmGroup: (runIds: string[]) => void;
  onNewRun: () => void;
}) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const groups = runs ? groupByCondition(runs) : new Map<string, RunListItem[]>();
  const hasMultipleGroups = groups.size > 1;
  const selectedCompareSet = useMemo(() => new Set(selectedCompareIds), [selectedCompareIds]);

  function toggleCollapsed(condition: string) {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(condition)) {
        next.delete(condition);
      } else {
        next.add(condition);
      }
      return next;
    });
  }

  return (
    <aside className="sidebar">
      <button
        type="button"
        className="sidebar__new-btn"
        onClick={onNewRun}
        title="New Run"
      >
        +
      </button>
      <div className="sidebar__sep" />

      {hasMultipleGroups ? (
        Array.from(groups.entries()).map(([condition, groupRuns]) => {
          const tooltip = groupTooltip(condition, groupRuns, analysesById);
          const groupSelected = groupRuns.every((run) => selectedCompareSet.has(run.runId));
            const collapsed = collapsedGroups.has(condition);
            return (
            <div key={condition} className="sidebar__group">
              <div
                className="sidebar__group-head"
                onMouseEnter={() => onWarmGroup(groupRuns.map((run) => run.runId))}
                onFocus={() => onWarmGroup(groupRuns.map((run) => run.runId))}
              >
                <button
                  type="button"
                  className="sidebar__group-label"
                  onClick={() => toggleCollapsed(condition)}
                  title={condition}
                >
                  <span className={groupStatusClass(tooltip.status)} />
                  <span>{condition.slice(0, 7)}</span>
                  <span className="sidebar__group-count">{groupRuns.length}</span>
                </button>
                <button
                  type="button"
                  className="sidebar__group-select"
                  data-selected={String(groupSelected)}
                  onClick={() => onSelectGroup(groupRuns.map((run) => run.runId))}
                  title="Compare this condition"
                >
                  {groupSelected ? "✓" : "Cmp"}
                </button>
                <div className="run-tooltip sidebar__group-tooltip">
                  <div className="run-tooltip__name">{tooltip.condition}</div>
                  <div className="run-tooltip__meta">{tooltip.status} · {groupRuns.length} runs</div>
                  <div className="run-tooltip__meta">{tooltip.totalGames} games · {tooltip.totalOffers} offers</div>
                  <div className="run-tooltip__meta">
                    Avg Gini {tooltip.avgGini === null ? "—" : tooltip.avgGini.toFixed(3)}
                  </div>
                </div>
              </div>
              {!collapsed && (
                <div className="sidebar__group-runs">
                  {groupRuns.map((run) => (
                    <RunButton
                      key={run.runId}
                      run={run}
                      selectedRunId={selectedRunId}
                      selectedCompare={selectedCompareSet.has(run.runId)}
                      grouped
                      onSelect={onSelect}
                      onToggleCompare={onToggleCompare}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })
      ) : (
        runs?.map((run) => (
          <RunButton
            key={run.runId}
            run={run}
            selectedRunId={selectedRunId}
            selectedCompare={selectedCompareSet.has(run.runId)}
            onSelect={onSelect}
            onToggleCompare={onToggleCompare}
          />
        ))
      )}

      {!runs?.length && <div className="empty sidebar__empty">—</div>}
    </aside>
  );
}
