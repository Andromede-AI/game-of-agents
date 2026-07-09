import type { RunAnalysisSummary, RunListItem } from "./types";

export type CompareMetric = {
  id: string;
  label: string;
  getValue: (run: RunListItem, analysis?: RunAnalysisSummary) => number | string | null | undefined;
  analysisDependent?: boolean;
  format?: (value: number | string | null | undefined) => string;
};

export const COMPARE_METRICS: CompareMetric[] = [
  { id: "games", label: "Games", getValue: (run) => run.gameCount },
  { id: "agents", label: "Agents", getValue: (run) => run.agentCount },
  { id: "botsSubmitted", label: "Bots submitted", analysisDependent: true, getValue: (_run, analysis) => analysis?.botsSubmitted },
  { id: "offers", label: "Offers", analysisDependent: true, getValue: (_run, analysis) => analysis?.marketplace.totalOffers },
  { id: "purchases", label: "Purchases", analysisDependent: true, getValue: (_run, analysis) => analysis?.marketplace.totalPurchases },
  { id: "chatMessageCount", label: "Chat messages", analysisDependent: true, getValue: (_run, analysis) => analysis?.chatMessageCount },
  {
    id: "giniCoefficient",
    label: "Gini coefficient",
    analysisDependent: true,
    getValue: (_run, analysis) => analysis?.giniCoefficient,
    format: (value) => formatNumber(value, 3),
  },
  {
    id: "avgAggression",
    label: "Avg aggression",
    analysisDependent: true,
    getValue: (_run, analysis) => analysis?.avgAggression,
    format: (value) => formatNumber(value, 3),
  },
  {
    id: "sameModelPurchasePct",
    label: "Same-model purchases",
    analysisDependent: true,
    getValue: (_run, analysis) => analysis?.marketplace.sameModelPurchasePct,
    format: (value) => formatPercent(value),
  },
  {
    id: "topAgentElo",
    label: "Top agent ELO",
    analysisDependent: true,
    getValue: (_run, analysis) => analysis?.topAgentElo,
    format: (value) => formatNumber(value, 0),
  },
];

export function formatNumber(value: number | string | null | undefined, digits: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return value.toFixed(digits);
}

export function formatPercent(value: number | string | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return `${value.toFixed(0)}%`;
}

export function formatMetric(metric: CompareMetric, value: number | string | null | undefined) {
  if (metric.format) {
    return metric.format(value);
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toString() : value.toFixed(1);
  }
  if (typeof value === "string" && value) {
    return value;
  }
  return "—";
}

export function calculateMetricDelta(
  baselineValue: number | string | null | undefined,
  value: number | string | null | undefined,
) {
  if (
    typeof baselineValue !== "number" ||
    !Number.isFinite(baselineValue) ||
    typeof value !== "number" ||
    !Number.isFinite(value)
  ) {
    return null;
  }
  const absolute = value - baselineValue;
  const percent = baselineValue === 0 ? null : (absolute / baselineValue) * 100;
  return { absolute, percent };
}

export function buildCompareCsvRows(
  runs: RunListItem[],
  analysesById: Record<string, RunAnalysisSummary | undefined>,
  baselineRunId: string | null,
) {
  return runs.map((run) => {
    const analysis = analysesById[run.runId];
    const row: Record<string, string | number | boolean | null> = {
      runId: run.runId,
      name: run.name,
      status: run.status,
      baseline: run.runId === baselineRunId,
    };
    for (const metric of COMPARE_METRICS) {
      row[metric.label] = metric.getValue(run, analysis) ?? null;
    }
    return row;
  });
}
