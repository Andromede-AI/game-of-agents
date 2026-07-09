"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AGENT_COLOR_VARS } from "./content-insights";
import { buildCompareCsvRows, calculateMetricDelta, COMPARE_METRICS, formatMetric } from "./compare-metrics";
import { serializeCsv } from "./csv";
import { exportNodeAsPng, exportZipBundle, printNodeAsReport, renderNodeToBlob } from "./export-utils";
import type { ComparePreset, CompareResourceState, RunAnalysisSummary, RunDashboard, RunListItem } from "./types";

const PRESET_STORAGE_KEY = "dashboard.comparePresets.v1";

function sortPresets(presets: ComparePreset[]) {
  return [...presets].sort((left, right) => right.updatedAt - left.updatedAt);
}

function readStoredPresets() {
  if (typeof window === "undefined") {
    return [] as ComparePreset[];
  }
  try {
    const raw = window.localStorage.getItem(PRESET_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as ComparePreset[];
    return sortPresets(Array.isArray(parsed) ? parsed : []);
  } catch {
    return [];
  }
}

function writeStoredPresets(presets: ComparePreset[]) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PRESET_STORAGE_KEY, JSON.stringify(sortPresets(presets)));
}

function formatNumber(value: number, digits = 1) {
  return value.toFixed(digits);
}

function formatDelta(delta: { absolute: number; percent: number | null }) {
  const absolutePrefix = delta.absolute > 0 ? "+" : "";
  const percentPrefix = delta.percent !== null && delta.percent > 0 ? "+" : "";
  if (delta.percent === null || !Number.isFinite(delta.percent)) {
    return `Δ ${absolutePrefix}${formatNumber(delta.absolute)}`;
  }
  return `Δ ${absolutePrefix}${formatNumber(delta.absolute)} (${percentPrefix}${formatNumber(delta.percent)}%)`;
}

function CompareTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean;
  label?: string | number;
  payload?: Array<{ color?: string; name?: string; value?: number | string | null }>;
}) {
  if (!active || label === undefined || !payload?.length) {
    return null;
  }
  const rows = payload.filter((entry) => typeof entry.value === "number");
  if (!rows.length) {
    return null;
  }
  return (
    <div className="elo-chart__tooltip">
      <div className="elo-chart__tooltip-label">Run progress: {label}%</div>
      <div className="elo-chart__tooltip-list">
        {rows.map((entry) => (
          <div key={String(entry.name)} className="elo-chart__tooltip-row">
            <span className="elo-chart__tooltip-name">
              <span
                className="elo-chart__tooltip-dot"
                style={{ background: entry.color ?? "var(--accent)" }}
              />
              {entry.name}
            </span>
            <span className="elo-chart__tooltip-value">{Number(entry.value).toFixed(1)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function resourceLabel<T>(resource: CompareResourceState<T> | undefined) {
  if (!resource || resource.status === "idle") {
    return "queued";
  }
  if (resource.status === "loading") {
    return resource.data ? "refreshing" : "loading";
  }
  if (resource.status === "error") {
    return resource.data ? "stale" : "error";
  }
  return "ready";
}

function makePresetId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `preset-${Date.now()}`;
}

export function CompareTab({
  runs,
  selectedCompareIds,
  analysesById,
  dashboardsById,
  onRemove,
  onRetry,
  onReplaceSelection,
}: {
  runs: RunListItem[];
  selectedCompareIds: string[];
  analysesById: Record<string, CompareResourceState<RunAnalysisSummary> | undefined>;
  dashboardsById: Record<string, CompareResourceState<RunDashboard> | undefined>;
  onRemove: (runId: string) => void;
  onRetry: (runId: string) => void;
  onReplaceSelection: (runIds: string[]) => void;
}) {
  const chartRef = useRef<HTMLElement | null>(null);
  const reportRef = useRef<HTMLDivElement | null>(null);
  const [baselineRunId, setBaselineRunId] = useState<string | null>(null);
  const [presets, setPresets] = useState<ComparePreset[]>([]);
  const [activityMessage, setActivityMessage] = useState<string | null>(null);

  const selectedRuns = useMemo(
    () =>
      selectedCompareIds
        .map((runId) => runs.find((run) => run.runId === runId))
        .filter((run): run is RunListItem => Boolean(run)),
    [runs, selectedCompareIds],
  );

  useEffect(() => {
    setPresets(readStoredPresets());
  }, []);

  useEffect(() => {
    if (!selectedRuns.length) {
      setBaselineRunId(null);
      return;
    }
    if (!baselineRunId || !selectedRuns.some((run) => run.runId === baselineRunId)) {
      setBaselineRunId(selectedRuns[0].runId);
    }
  }, [baselineRunId, selectedRuns]);

  const baselineRun = useMemo(
    () => selectedRuns.find((run) => run.runId === baselineRunId) ?? selectedRuns[0] ?? null,
    [baselineRunId, selectedRuns],
  );

  const analysisDataById = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(analysesById).map(([runId, resource]) => [runId, resource?.data]),
      ) as Record<string, RunAnalysisSummary | undefined>,
    [analysesById],
  );

  const chartData = useMemo(() => {
    const rows = new Map<number, Record<string, number | string | null>>();
    for (const run of selectedRuns) {
      const snapshots = dashboardsById[run.runId]?.data?.snapshots ?? [];
      if (!snapshots.length) {
        continue;
      }
      snapshots.forEach((snapshot, index) => {
        const progress = snapshots.length <= 1 ? 100 : Math.round((index / (snapshots.length - 1)) * 100);
        const row = rows.get(progress) ?? { progress };
        row[run.runId] = typeof snapshot.bestRating === "number" ? snapshot.bestRating : null;
        rows.set(progress, row);
      });
    }
    return Array.from(rows.values()).sort((left, right) => Number(left.progress) - Number(right.progress));
  }, [dashboardsById, selectedRuns]);

  const analysisErrors = selectedRuns.filter((run) => analysesById[run.runId]?.status === "error");
  const chartErrors = selectedRuns.filter((run) => dashboardsById[run.runId]?.status === "error");
  const pendingAnalysisCount = selectedRuns.filter((run) => {
    const state = analysesById[run.runId];
    return !state || state.status === "loading" || state.status === "idle";
  }).length;
  const pendingChartCount = selectedRuns.filter((run) => {
    const state = dashboardsById[run.runId];
    return !state || state.status === "loading" || state.status === "idle";
  }).length;

  const statusLabel =
    analysisErrors.length || chartErrors.length
      ? `${analysisErrors.length + chartErrors.length} fetch issue${analysisErrors.length + chartErrors.length === 1 ? "" : "s"}`
      : pendingAnalysisCount || pendingChartCount
        ? `Refreshing ${pendingAnalysisCount + pendingChartCount} resource${pendingAnalysisCount + pendingChartCount === 1 ? "" : "s"}…`
        : "Data ready";

  const presetActionsDisabled = selectedRuns.length < 2;

  const handleSavePreset = () => {
    if (selectedRuns.length < 2) {
      return;
    }
    const suggested = `${selectedRuns[0]?.name ?? "Run"} vs ${selectedRuns[1]?.name ?? "Run"}`;
    const name = window.prompt("Preset name", suggested)?.trim();
    if (!name) {
      return;
    }
    const now = Date.now();
    const nextPreset: ComparePreset = {
      id: makePresetId(),
      name,
      runIds: selectedRuns.map((run) => run.runId),
      createdAt: now,
      updatedAt: now,
    };
    const nextPresets = sortPresets([nextPreset, ...presets]);
    setPresets(nextPresets);
    writeStoredPresets(nextPresets);
    setActivityMessage(`Saved preset "${name}".`);
  };

  const handleRenamePreset = (preset: ComparePreset) => {
    const name = window.prompt("Rename preset", preset.name)?.trim();
    if (!name || name === preset.name) {
      return;
    }
    const nextPresets = sortPresets(
      presets.map((item) =>
        item.id === preset.id
          ? {
              ...item,
              name,
              updatedAt: Date.now(),
            }
          : item,
      ),
    );
    setPresets(nextPresets);
    writeStoredPresets(nextPresets);
    setActivityMessage(`Renamed preset to "${name}".`);
  };

  const handleDeletePreset = (preset: ComparePreset) => {
    const nextPresets = presets.filter((item) => item.id !== preset.id);
    setPresets(nextPresets);
    writeStoredPresets(nextPresets);
    setActivityMessage(`Deleted preset "${preset.name}".`);
  };

  const handleLoadPreset = (preset: ComparePreset) => {
    const validIds = preset.runIds.filter((runId) => runs.some((run) => run.runId === runId));
    onReplaceSelection(validIds);
    setBaselineRunId(validIds[0] ?? null);
    setActivityMessage(`Loaded preset "${preset.name}".`);
  };

  const handleExportCsv = () => {
    if (!selectedRuns.length) {
      return;
    }
    const rows = buildCompareCsvRows(selectedRuns, analysisDataById, baselineRun?.runId ?? null);
    const csv = serializeCsv(rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "compare-metrics.csv";
    anchor.click();
    URL.revokeObjectURL(url);
    setActivityMessage("Exported compare CSV.");
  };

  const handleExportChart = async () => {
    if (!chartRef.current) {
      return;
    }
    await exportNodeAsPng(chartRef.current, "compare-chart.png", "Compare Rating Trajectories");
    setActivityMessage("Exported compare chart PNG.");
  };

  const handleExportBundle = async () => {
    if (!chartRef.current || !reportRef.current || !selectedRuns.length) {
      return;
    }
    const [chartBlob, reportBlob] = await Promise.all([
      renderNodeToBlob(chartRef.current, "Compare Rating Trajectories"),
      renderNodeToBlob(reportRef.current, "Compare Research Report"),
    ]);
    const csv = serializeCsv(
      buildCompareCsvRows(selectedRuns, analysisDataById, baselineRun?.runId ?? null),
    );
    await exportZipBundle("compare-research-bundle.zip", [
      { name: "compare-chart.png", data: chartBlob },
      { name: "compare-report.png", data: reportBlob },
      { name: "compare-metrics.csv", data: csv },
    ]);
    setActivityMessage("Exported compare research bundle.");
  };

  const handlePrintReport = async () => {
    if (!reportRef.current) {
      return;
    }
    await printNodeAsReport(reportRef.current, "Compare Research Report");
    setActivityMessage("Opened printable compare report.");
  };

  if (selectedRuns.length < 2) {
    return (
      <div className="compare-empty panel">
        <h3>Compare Runs</h3>
        <p>Select at least two runs from the sidebar to open the comparison view.</p>
        {presets.length > 0 && (
          <div className="compare-presets compare-presets--inline">
            {presets.map((preset) => (
              <div key={preset.id} className="compare-preset">
                <div>
                  <strong>{preset.name}</strong>
                  <span>{preset.runIds.length} runs</span>
                </div>
                <button type="button" className="btn btn--sm" onClick={() => handleLoadPreset(preset)}>
                  Load
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="compare-tab">
      <div className="compare-report" ref={reportRef}>
        <section className="compare-summary panel">
          <div className="compare-summary__header">
            <div>
              <h3>Selected Runs</h3>
              <p>Use the sidebar compare toggles or condition group actions to add and remove runs.</p>
            </div>
            <span className="compare-summary__status">{statusLabel}</span>
          </div>

          <div className="compare-summary__toolbar" data-export-ignore="true">
            <label className="compare-toolbar__field">
              <span>Baseline</span>
              <select
                value={baselineRun?.runId ?? ""}
                onChange={(event) => setBaselineRunId(event.target.value)}
              >
                {selectedRuns.map((run) => (
                  <option key={run.runId} value={run.runId}>
                    {run.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="compare-summary__actions">
              <button type="button" className="btn btn--sm" onClick={handleSavePreset} disabled={presetActionsDisabled}>
                Save Preset
              </button>
              <button type="button" className="btn btn--sm" onClick={() => {
                onReplaceSelection([]);
                setBaselineRunId(null);
                setActivityMessage("Cleared compare selection.");
              }}>
                Clear Compare
              </button>
              <button type="button" className="btn btn--sm" onClick={handleExportCsv}>
                Export CSV
              </button>
              <button type="button" className="btn btn--sm" onClick={() => void handleExportChart()}>
                Export PNG
              </button>
              <button type="button" className="btn btn--sm" onClick={() => void handleExportBundle()}>
                Research Bundle
              </button>
              <button type="button" className="btn btn--sm btn--accent" onClick={() => void handlePrintReport()}>
                Print PDF
              </button>
            </div>
          </div>

          {activityMessage && <div className="compare-summary__note">{activityMessage}</div>}

          {presets.length > 0 && (
            <div className="compare-presets" data-export-ignore="true">
              {presets.map((preset) => (
                <div key={preset.id} className="compare-preset">
                  <div className="compare-preset__meta">
                    <strong>{preset.name}</strong>
                    <span>{preset.runIds.length} runs</span>
                  </div>
                  <div className="compare-preset__actions">
                    <button type="button" className="btn btn--sm" onClick={() => handleLoadPreset(preset)}>
                      Load
                    </button>
                    <button type="button" className="btn btn--sm" onClick={() => handleRenamePreset(preset)}>
                      Rename
                    </button>
                    <button type="button" className="btn btn--sm" onClick={() => handleDeletePreset(preset)}>
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="compare-run-cards">
            {selectedRuns.map((run, index) => {
              const analysisResource = analysesById[run.runId];
              const dashboardResource = dashboardsById[run.runId];
              const hasError = analysisResource?.status === "error" || dashboardResource?.status === "error";
              return (
                <article key={run.runId} className="compare-run-card panel" data-error={String(hasError)}>
                  <div className="compare-run-card__header">
                    <div>
                      <strong>{run.name}</strong>
                      <div className="compare-run-card__meta">
                        {run.gameCount} games · {run.agentCount} agents
                        {baselineRun?.runId === run.runId ? " · baseline" : ""}
                      </div>
                    </div>
                    <span
                      className="chip"
                      data-active="true"
                      style={{ borderColor: AGENT_COLOR_VARS[index % AGENT_COLOR_VARS.length] }}
                    >
                      <span
                        className="chip__dot"
                        style={{ background: AGENT_COLOR_VARS[index % AGENT_COLOR_VARS.length] }}
                      />
                      {run.runId}
                    </span>
                  </div>
                  <div className="compare-run-card__statuses">
                    <span className="compare-run-card__status" data-state={analysisResource?.status ?? "idle"}>
                      Analysis {resourceLabel(analysisResource)}
                    </span>
                    <span className="compare-run-card__status" data-state={dashboardResource?.status ?? "idle"}>
                      Chart {resourceLabel(dashboardResource)}
                    </span>
                  </div>
                  {(analysisResource?.error || dashboardResource?.error) && (
                    <div className="compare-run-card__error">
                      {analysisResource?.error ?? dashboardResource?.error}
                    </div>
                  )}
                  <div className="compare-run-card__actions" data-export-ignore="true">
                    {hasError && (
                      <button type="button" className="btn btn--sm" onClick={() => onRetry(run.runId)}>
                        Retry
                      </button>
                    )}
                    <button type="button" className="btn btn--sm" onClick={() => onRemove(run.runId)}>
                      Remove
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="compare-table panel">
          <div className="compare-table__header">
            <div>
              <h3>Metrics</h3>
              <p>Run-level summaries use the analysis endpoint, with deltas anchored to the selected baseline.</p>
            </div>
          </div>
          <div className="compare-table__wrap">
            <table className="data-table compare-table__grid">
              <thead>
                <tr>
                  <th>Metric</th>
                  {selectedRuns.map((run) => (
                    <th key={run.runId}>
                      {run.name}
                      {baselineRun?.runId === run.runId ? " (baseline)" : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {COMPARE_METRICS.map((metric) => {
                  const baselineValue = baselineRun
                    ? metric.getValue(baselineRun, analysesById[baselineRun.runId]?.data)
                    : null;
                  return (
                    <tr key={metric.id}>
                      <td>{metric.label}</td>
                      {selectedRuns.map((run) => {
                        const analysisResource = analysesById[run.runId];
                        const value = metric.getValue(run, analysisResource?.data);
                        const delta =
                          baselineRun?.runId && baselineRun.runId !== run.runId
                            ? calculateMetricDelta(baselineValue, value)
                            : null;
                        const needsAnalysis = Boolean(metric.analysisDependent);
                        const stateLabel =
                          needsAnalysis && !analysisResource?.data
                            ? resourceLabel(analysisResource)
                            : null;
                        return (
                          <td key={`${metric.id}-${run.runId}`}>
                            <div className="compare-cell">
                              <strong>{formatMetric(metric, value)}</strong>
                              {baselineRun?.runId === run.runId ? (
                                <small>Baseline</small>
                              ) : delta ? (
                                <small>{formatDelta(delta)}</small>
                              ) : stateLabel ? (
                                <small data-state={analysisResource?.status ?? "idle"}>{stateLabel}</small>
                              ) : analysisResource?.status === "loading" ? (
                                <small data-state="loading">Refreshing</small>
                              ) : analysisResource?.status === "error" ? (
                                <small data-state="error">Stale data</small>
                              ) : null}
                            </div>
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section className="elo-chart compare-chart panel" ref={chartRef}>
          <div className="elo-chart__header">
            <div>
              <h2>Compare Rating Trajectories</h2>
              <p>Sampled best-rating snapshots over run progress.</p>
            </div>
            <div className="compare-chart__actions" data-export-ignore="true">
              <button type="button" className="btn btn--sm" onClick={() => void handleExportChart()}>
                Export PNG
              </button>
            </div>
          </div>
          {(chartErrors.length > 0 || pendingChartCount > 0) && (
            <div className="compare-chart__notices">
              {pendingChartCount > 0 && (
                <span>{pendingChartCount} chart request{pendingChartCount === 1 ? "" : "s"} still loading.</span>
              )}
              {chartErrors.map((run) => (
                <span key={run.runId}>
                  {run.name}: {dashboardsById[run.runId]?.error ?? "chart fetch failed"}
                </span>
              ))}
            </div>
          )}
          <div className="elo-chart__chips">
            {selectedRuns.map((run, index) => (
              <span key={run.runId} className="chip" data-active="true">
                <span
                  className="chip__dot"
                  style={{ background: AGENT_COLOR_VARS[index % AGENT_COLOR_VARS.length] }}
                />
                {run.name}
              </span>
            ))}
          </div>
          <div className="elo-chart__body">
            {chartData.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%" debounce={150}>
                <LineChart data={chartData}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis
                    dataKey="progress"
                    tickFormatter={(value) => `${value}%`}
                    tickLine={false}
                    axisLine={false}
                    tick={{ fill: "#848494", fontSize: 11 }}
                  />
                  <YAxis tickLine={false} axisLine={false} tick={{ fill: "#848494", fontSize: 11 }} />
                  <Tooltip content={<CompareTooltip />} />
                  {selectedRuns.map((run, index) => (
                    <Line
                      key={run.runId}
                      type="linear"
                      dataKey={run.runId}
                      stroke={AGENT_COLOR_VARS[index % AGENT_COLOR_VARS.length]}
                      strokeWidth={2.3}
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                      name={run.name}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="empty">No sampled snapshot history loaded yet.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
