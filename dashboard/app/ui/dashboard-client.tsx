"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  CommentContentFilter,
  CompareResourceState,
  RunAnalysisSummary,
  RunDashboard,
  RunListItem,
  TabId,
} from "./types";
import { TopNav } from "./top-nav";
import { RunSidebar } from "./run-sidebar";
import { RunHeader } from "./run-header";
import { RatingChart } from "./elo-chart";
import { TabBar } from "./tab-bar";
import { TournamentTab } from "./tournament-tab";
import { MarketplaceTab } from "./marketplace-tab";
import { AgentsTab } from "./agents-tab";
import { CommentsTab } from "./comments-tab";
import { buildCommentFeed } from "./comment-feed";
import { EventsTab } from "./events-tab";
import { LiveChatPane } from "./live-chat-pane";
import { NewRunModal } from "./new-run-modal";
import { effectiveRunStatus } from "./run-status";
import { normalizeRunState } from "./state";
import { CompareTab } from "./compare-tab";
import type { HighlightCategory } from "./content-insights";
import { buildDashboardUrlSearch, parseDashboardUrlState } from "./dashboard-url-state";
import {
  beginCompareResourceFetch,
  failCompareResourceFetch,
  needsCompareResourceFetch,
  pruneCompareResourceMap,
  succeedCompareResourceFetch,
} from "./compare-state";

type BackendRunSummary = {
  run_id: string;
  name: string;
  description: string;
  status: string;
  created_at: string | number;
  started_at?: string | number | null;
  finished_at?: string | number | null;
  updated_at?: string | number;
  agents?: Record<string, unknown>;
  bots?: Record<string, unknown>;
  games?: Record<string, unknown>;
  offers?: Record<string, unknown>;
  best_rating?: number | null;
  final_scores?: Record<string, number> | null;
  payouts?: Record<string, number> | null;
  config?: {
    name?: string;
    description?: string;
  } | null;
  agent_count?: number;
  bot_count?: number;
  game_count?: number;
  offer_count?: number;
};

function parseTs(value: string | number | null | undefined) {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value) return new Date(value).getTime();
  return null;
}

function toRunListItem(run: BackendRunSummary): RunListItem {
  const name = run.name ?? run.config?.name ?? run.run_id;
  const description = run.description ?? run.config?.description ?? "";
  return {
    runId: run.run_id,
    name,
    description,
    status: run.status,
    createdAt: parseTs(run.created_at) ?? Date.now(),
    startedAt: parseTs(run.started_at),
    finishedAt: parseTs(run.finished_at),
    updatedAt: parseTs(run.updated_at) ?? parseTs(run.created_at) ?? Date.now(),
    agentCount: run.agent_count ?? Object.keys(run.agents ?? {}).length,
    botCount: run.bot_count ?? Object.keys(run.bots ?? {}).length,
    gameCount: run.game_count ?? Object.keys(run.games ?? {}).length,
    offerCount: run.offer_count ?? Object.keys(run.offers ?? {}).length,
    bestAgentId: null,
    bestRating: typeof run.best_rating === "number" ? run.best_rating : null,
  };
}

function toRunListItemFromDashboard(dashboard: RunDashboard): RunListItem {
  const run = dashboard.run;
  return {
    runId: run.runId,
    name: run.name,
    description: run.description,
    status: run.status,
    createdAt: run.createdAt,
    startedAt: run.startedAt,
    finishedAt: run.finishedAt,
    updatedAt: run.updatedAt,
    agentCount: run.agentCount,
    botCount: run.botCount,
    gameCount: run.gameCount,
    offerCount: run.offerCount,
    bestAgentId: run.bestAgentId,
    bestRating: run.bestRating,
  };
}

export function DashboardClient() {
  const ACTIVE_SNAPSHOT_LIMIT = 800;
  const FINISHED_SAMPLE_SIZE = 180;
  const [runs, setRuns] = useState<RunListItem[] | undefined>(undefined);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<RunDashboard | null | undefined>(undefined);
  const [activeTab, setActiveTab] = useState<TabId>("tournament");
  const [showNewRun, setShowNewRun] = useState(false);
  const [seedConfig, setSeedConfig] = useState<Record<string, unknown> | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [deletePendingId, setDeletePendingId] = useState<string | null>(null);
  const [stopPendingId, setStopPendingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedCompareIds, setSelectedCompareIds] = useState<string[]>([]);
  const [runAnalysisById, setRunAnalysisById] = useState<Record<string, CompareResourceState<RunAnalysisSummary> | undefined>>({});
  const [compareDashboardsById, setCompareDashboardsById] = useState<Record<string, CompareResourceState<RunDashboard> | undefined>>({});
  const [commentAuthorFilter, setCommentAuthorFilter] = useState<string>("all");
  const [commentContentFilter, setCommentContentFilter] = useState<CommentContentFilter>("all");
  const [logAgentId, setLogAgentId] = useState<string | null>(null);
  const [highlightFilter, setHighlightFilter] = useState<"all" | HighlightCategory>("all");
  const [copyLinkStatus, setCopyLinkStatus] = useState<"idle" | "copied" | "error">("idle");
  const analysisLoadingRef = useRef(new Set<string>());
  const compareDashboardLoadingRef = useRef(new Set<string>());
  const urlStateReadyRef = useRef(false);
  const copyLinkTimerRef = useRef<number | undefined>(undefined);

  const applyUrlState = useCallback((search: string) => {
    const parsed = parseDashboardUrlState(search);
    setActiveTab(parsed.activeTab ?? "tournament");
    setSelectedRunId(parsed.selectedRunId ?? null);
    setSelectedCompareIds(Array.from(new Set(parsed.selectedCompareIds ?? [])));
    setCommentAuthorFilter(parsed.commentAuthorFilter ?? "all");
    setCommentContentFilter(parsed.commentContentFilter ?? "all");
    setLogAgentId(parsed.logAgentId ?? null);
    setHighlightFilter(parsed.highlightFilter ?? "all");
  }, []);

  const loadRuns = useCallback(async () => {
    const response = await fetch("/api/runs", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Run list failed with status ${response.status}.`);
    }
    const payload = (await response.json()) as BackendRunSummary[];
    setRuns(payload.map(toRunListItem).sort((left, right) => right.createdAt - left.createdAt));
  }, []);

  useEffect(() => {
    void loadRuns();
    let cancelled = false;
    let timer: number | undefined;

    const getRunListDelay = () => {
      // Check if any run is active — if not, poll much less frequently
      const hasActiveRun = runs?.some(
        (r) => r.status === "running" || r.status === "starting" || r.status === "stopping",
      );
      if (!hasActiveRun) {
        // No active runs — poll infrequently just to catch new launches
        return document.visibilityState === "hidden" ? 300000 : 120000; // 5min hidden, 2min visible
      }
      return document.visibilityState === "hidden" ? 30000 : 10000; // 30s hidden, 10s visible
    };

    const schedule = (delayMs: number) => {
      timer = window.setTimeout(() => {
        void loadRuns().finally(() => {
          if (!cancelled) {
            schedule(getRunListDelay());
          }
        });
      }, delayMs);
    };

    const handleVisibility = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      schedule(getRunListDelay());
    };

    schedule(getRunListDelay());
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [loadRuns]);

  useEffect(() => {
    if (!runs?.length) {
      return;
    }
    if (!selectedRunId || !runs.some((run) => run.runId === selectedRunId)) {
      setSelectedRunId(runs[0].runId);
    }
  }, [runs, selectedRunId]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const syncFromLocation = () => {
      applyUrlState(window.location.search);
      urlStateReadyRef.current = true;
    };
    syncFromLocation();
    window.addEventListener("popstate", syncFromLocation);
    return () => {
      window.removeEventListener("popstate", syncFromLocation);
    };
  }, [applyUrlState]);

  useEffect(() => {
    return () => {
      if (copyLinkTimerRef.current !== undefined) {
        window.clearTimeout(copyLinkTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    setDeleteError(null);
    setActionError(null);
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      setDashboard(undefined);
      return;
    }

    setDashboard(undefined);
    let cancelled = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let lastStatus = "running";
    let requestSeq = 0;

    const isActiveStatus = () => lastStatus === "running" || lastStatus === "stopping";

    const nextDelay = () => {
      const active = isActiveStatus();
      if (!active) {
        // Finished runs never change — don't poll at all.
        // Data was already loaded on initial fetch.
        return Infinity;
      }
      return document.visibilityState === "hidden" ? 20000 : 5000;
    };

    const schedule = (delayMs: number) => {
      timer = window.setTimeout(() => {
        void loadDashboard();
      }, delayMs);
    };

    const loadDashboard = async () => {
      const seq = ++requestSeq;
      controller?.abort();
      controller = new AbortController();
      const query = isActiveStatus()
        ? `snapshot_limit=${ACTIVE_SNAPSHOT_LIMIT}&event_limit=160`
        : `sample_full_history=true&sample_size=${FINISHED_SAMPLE_SIZE}&event_limit=160`;
      const requestedActiveStatus = isActiveStatus();
      let refetchSampledHistory = false;
      try {
        const response = await fetch(
          `/api/runs/${selectedRunId}/dashboard?${query}`,
          {
            cache: "no-store",
            signal: controller.signal,
          },
        );
        if (!response.ok) {
          throw new Error(`Run dashboard failed with status ${response.status}.`);
        }
        const payload = (await response.json()) as RunDashboard;
        if (cancelled) {
          return;
        }
        setDashboard(payload);
        lastStatus = effectiveRunStatus(payload.run?.status, payload.run?.finishedAt);
        refetchSampledHistory = requestedActiveStatus && (lastStatus === "finished" || lastStatus === "failed");
      } catch (error) {
        if (cancelled) {
          return;
        }
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        lastStatus = "pending";
      } finally {
        if (!cancelled && seq === requestSeq) {
          schedule(refetchSampledHistory ? 0 : nextDelay());
        }
      }
    };

    const handleVisibility = () => {
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      schedule(0);
    };

    void loadDashboard();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== undefined) {
        window.clearTimeout(timer);
      }
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [selectedRunId]);

  const handleDelete = useCallback(
    async (runId: string) => {
      const target = runs?.find((item) => item.runId === runId);
      const confirmed = window.confirm(
        `Delete run "${target?.name ?? runId}"? This removes it from the backend and dashboard.`,
      );
      if (!confirmed) {
        return;
      }
      setDeletePendingId(runId);
      setDeleteError(null);
      try {
        const response = await fetch(`/api/runs/${runId}`, { method: "DELETE" });
        if (!response.ok && response.status !== 404) {
          let detail = `Delete failed with status ${response.status}.`;
          try {
            const payload = (await response.json()) as { detail?: string };
            if (payload.detail) {
              detail = payload.detail;
            }
          } catch {
            // Ignore non-JSON proxy error bodies.
          }
          throw new Error(detail);
        }
        if (selectedRunId === runId) {
          const remaining = runs?.filter((r) => r.runId !== runId);
          setSelectedRunId(remaining?.length ? remaining[0].runId : null);
        }
        await loadRuns();
      } catch (error) {
        setDeleteError(error instanceof Error ? error.message : "Run deletion failed.");
      } finally {
        setDeletePendingId(null);
      }
    },
    [loadRuns, runs, selectedRunId],
  );

  const handleStop = useCallback(async (runId: string) => {
    setStopPendingId(runId);
    setActionError(null);
    try {
      const response = await fetch(`/api/runs/${runId}/stop`, { method: "POST" });
      if (!response.ok) {
        let detail = `Cancel failed with status ${response.status}.`;
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload.detail) {
            detail = payload.detail;
          }
        } catch {
          // Ignore non-JSON proxy error bodies.
        }
        throw new Error(detail);
      }
      await loadRuns();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Run cancellation failed.");
    } finally {
      setStopPendingId(null);
    }
  }, [loadRuns]);

  const run = dashboard?.run;
  const config = run?.config;
  const state = useMemo(() => normalizeRunState(run?.state), [run?.state]);
  const displayStatus = effectiveRunStatus(run?.status, run?.finishedAt);
  const sidebarRuns = useMemo(() => {
    if (!runs) {
      return runs;
    }
    if (!dashboard?.run || runs.some((item) => item.runId === dashboard.run.runId)) {
      return runs;
    }
    return [toRunListItemFromDashboard(dashboard), ...runs];
  }, [dashboard, runs]);
  const commentFeedMessages = useMemo(
    () => buildCommentFeed(dashboard?.comments ?? [], dashboard?.feedMessages ?? []),
    [dashboard?.comments, dashboard?.feedMessages],
  );
  const sidebarRunById = useMemo(
    () => Object.fromEntries((sidebarRuns ?? []).map((item) => [item.runId, item])),
    [sidebarRuns],
  );
  const analysisDataById = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(runAnalysisById).map(([runId, resource]) => [runId, resource?.data]),
      ) as Record<string, RunAnalysisSummary | undefined>,
    [runAnalysisById],
  );

  const handleNewRun = useCallback(() => {
    setSeedConfig(null);
    setShowNewRun(true);
  }, []);

  const loadRunAnalysis = useCallback(async (runId: string, sourceUpdatedAt: number) => {
    if (analysisLoadingRef.current.has(runId)) {
      return;
    }
    analysisLoadingRef.current.add(runId);
    setRunAnalysisById((current) => ({
      ...current,
      [runId]: beginCompareResourceFetch(current[runId], sourceUpdatedAt),
    }));
    try {
      const response = await fetch(`/api/runs/${runId}/analysis`, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Run analysis failed with status ${response.status}.`);
      }
      const payload = (await response.json()) as RunAnalysisSummary;
      setRunAnalysisById((current) => ({
        ...current,
        [runId]: succeedCompareResourceFetch(payload, sourceUpdatedAt),
      }));
    } catch (error) {
      setRunAnalysisById((current) => ({
        ...current,
        [runId]: failCompareResourceFetch(
          current[runId],
          error instanceof Error ? error.message : "Run analysis failed.",
          sourceUpdatedAt,
        ),
      }));
    } finally {
      analysisLoadingRef.current.delete(runId);
    }
  }, []);

  const loadCompareDashboard = useCallback(async (runId: string, sourceUpdatedAt: number) => {
    if (compareDashboardLoadingRef.current.has(runId)) {
      return;
    }
    compareDashboardLoadingRef.current.add(runId);
    setCompareDashboardsById((current) => ({
      ...current,
      [runId]: beginCompareResourceFetch(current[runId], sourceUpdatedAt),
    }));
    try {
      const response = await fetch(
        `/api/runs/${runId}/dashboard?sample_full_history=true&sample_size=${FINISHED_SAMPLE_SIZE}&event_limit=1`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(`Compare dashboard failed with status ${response.status}.`);
      }
      const payload = (await response.json()) as RunDashboard;
      setCompareDashboardsById((current) => ({
        ...current,
        [runId]: succeedCompareResourceFetch(payload, sourceUpdatedAt),
      }));
    } catch (error) {
      setCompareDashboardsById((current) => ({
        ...current,
        [runId]: failCompareResourceFetch(
          current[runId],
          error instanceof Error ? error.message : "Compare dashboard failed.",
          sourceUpdatedAt,
        ),
      }));
    } finally {
      compareDashboardLoadingRef.current.delete(runId);
    }
  }, []);

  const handleDuplicate = useCallback(() => {
    if (!config) {
      return;
    }
    setSeedConfig({
      ...config,
      name: `${config.name}-copy`,
      description: config.description ?? "",
      agents: (config.agents ?? []).map((agent) => ({
        ...agent,
        command: agent.command ? [...agent.command] : undefined,
      })),
    });
    setShowNewRun(true);
  }, [config]);

  const agentModels = useMemo(
    () =>
      Object.fromEntries(
        (config?.agents ?? []).map((agent) => [agent.agent_id, agent.model ?? null]),
      ),
    [config?.agents],
  );

  const handleToggleCompare = useCallback((runId: string) => {
    setSelectedCompareIds((current) =>
      current.includes(runId)
        ? current.filter((id) => id !== runId)
        : [...current, runId],
    );
  }, []);

  const handleSetLogAgentId = useCallback((nextAgentId: string | null) => {
    setLogAgentId(nextAgentId);
    setHighlightFilter("all");
  }, []);

  const handleSelectGroup = useCallback((runIds: string[]) => {
    setSelectedCompareIds((current) => {
      const alreadySelected = runIds.every((runId) => current.includes(runId));
      if (alreadySelected) {
        return current.filter((runId) => !runIds.includes(runId));
      }
      return [...current, ...runIds.filter((runId) => !current.includes(runId))];
    });
    setActiveTab("compare");
  }, []);

  const handleWarmCompareGroup = useCallback((runIds: string[]) => {
    runIds.forEach((runId) => {
      const runItem = sidebarRunById[runId];
      if (runItem && needsCompareResourceFetch(runAnalysisById[runId], runItem.updatedAt)) {
        void loadRunAnalysis(runId, runItem.updatedAt);
      }
    });
  }, [loadRunAnalysis, runAnalysisById, sidebarRunById]);

  useEffect(() => {
    const knownRunIds = new Set((sidebarRuns ?? []).map((run) => run.runId));
    setSelectedCompareIds((current) => {
      const next = current.filter((runId) => knownRunIds.has(runId));
      return next.length === current.length ? current : next;
    });
    setRunAnalysisById((current) => pruneCompareResourceMap(current, knownRunIds));
    setCompareDashboardsById((current) => pruneCompareResourceMap(current, knownRunIds));
  }, [sidebarRuns]);

  useEffect(() => {
    const selectedRuns = selectedCompareIds
      .map((runId) => sidebarRunById[runId])
      .filter((run): run is RunListItem => Boolean(run));
    selectedRuns.forEach((run) => {
      if (needsCompareResourceFetch(runAnalysisById[run.runId], run.updatedAt)) {
        void loadRunAnalysis(run.runId, run.updatedAt);
      }
    });
  }, [loadRunAnalysis, runAnalysisById, selectedCompareIds, sidebarRunById]);

  useEffect(() => {
    if (activeTab !== "compare") {
      return;
    }
    const selectedRuns = selectedCompareIds
      .map((runId) => sidebarRunById[runId])
      .filter((run): run is RunListItem => Boolean(run));
    selectedRuns.forEach((run) => {
      if (needsCompareResourceFetch(compareDashboardsById[run.runId], run.updatedAt)) {
        void loadCompareDashboard(run.runId, run.updatedAt);
      }
    });
  }, [activeTab, compareDashboardsById, loadCompareDashboard, selectedCompareIds, sidebarRunById]);

  useEffect(() => {
    if (!config?.agents?.length || !logAgentId) {
      return;
    }
    const knownAgentIds = new Set(config.agents.map((agent) => agent.agent_id));
    if (!knownAgentIds.has(logAgentId)) {
      setLogAgentId(null);
      setHighlightFilter("all");
    }
  }, [config?.agents, logAgentId]);

  useEffect(() => {
    if (!urlStateReadyRef.current || typeof window === "undefined") {
      return;
    }
    const search = buildDashboardUrlSearch({
      activeTab,
      selectedRunId,
      selectedCompareIds,
      commentAuthorFilter,
      commentContentFilter,
      logAgentId,
      highlightFilter,
    });
    const nextUrl = search ? `${window.location.pathname}?${search}` : window.location.pathname;
    window.history.replaceState(null, "", nextUrl);
  }, [
    activeTab,
    commentAuthorFilter,
    commentContentFilter,
    highlightFilter,
    logAgentId,
    selectedCompareIds,
    selectedRunId,
  ]);

  const handleCopyLink = useCallback(async () => {
    if (typeof window === "undefined") {
      return;
    }
    try {
      const search = buildDashboardUrlSearch({
        activeTab,
        selectedRunId,
        selectedCompareIds,
        commentAuthorFilter,
        commentContentFilter,
        logAgentId,
        highlightFilter,
      });
      const url = new URL(window.location.href);
      url.search = search;
      await navigator.clipboard.writeText(url.toString());
      setCopyLinkStatus("copied");
    } catch {
      setCopyLinkStatus("error");
    }
    if (copyLinkTimerRef.current !== undefined) {
      window.clearTimeout(copyLinkTimerRef.current);
    }
    copyLinkTimerRef.current = window.setTimeout(() => {
      setCopyLinkStatus("idle");
    }, 2400);
  }, [
    activeTab,
    commentAuthorFilter,
    commentContentFilter,
    highlightFilter,
    logAgentId,
    selectedCompareIds,
    selectedRunId,
  ]);

  const handleRetryCompareRun = useCallback((runId: string) => {
    const runItem = sidebarRunById[runId];
    if (!runItem) {
      return;
    }
    void loadRunAnalysis(runId, runItem.updatedAt);
    void loadCompareDashboard(runId, runItem.updatedAt);
  }, [loadCompareDashboard, loadRunAnalysis, sidebarRunById]);

  return (
    <main className="shell">
      <div className="layout">
        <TopNav
          runName={run?.name}
          status={displayStatus}
          startedAt={run?.startedAt}
          finishedAt={run?.finishedAt}
          durationMinutes={config?.duration_minutes}
          onNewRun={handleNewRun}
          onToggleChat={() => setChatOpen((v) => !v)}
          chatVisible={chatOpen}
          onCopyLink={() => void handleCopyLink()}
          copyLinkStatus={copyLinkStatus}
        />

        <RunSidebar
          runs={sidebarRuns}
          selectedRunId={selectedRunId}
          selectedCompareIds={selectedCompareIds}
          analysesById={analysisDataById}
          onSelect={setSelectedRunId}
          onToggleCompare={handleToggleCompare}
          onSelectGroup={handleSelectGroup}
          onWarmGroup={handleWarmCompareGroup}
          onNewRun={handleNewRun}
        />

        <section className="main">
          {activeTab === "compare" ? (
            <>
              <TabBar active={activeTab} onChange={setActiveTab} />
              <div className="tab-content">
                <CompareTab
                  runs={sidebarRuns ?? []}
                  selectedCompareIds={selectedCompareIds}
                  analysesById={runAnalysisById}
                  dashboardsById={compareDashboardsById}
                  onRemove={handleToggleCompare}
                  onRetry={handleRetryCompareRun}
                  onReplaceSelection={setSelectedCompareIds}
                />
              </div>
            </>
          ) : run && config ? (
            <>
              <RunHeader
                name={run.name}
                status={run.status}
                finishedAt={run.finishedAt}
                config={config}
                state={state}
                agents={run.leaderboard.agents}
                agentCount={run.agentCount}
                botCount={run.botCount}
                gameCount={run.gameCount}
                finalScores={run.finalScores}
                payouts={run.payouts}
                stopPending={stopPendingId === run.runId}
                deletePending={deletePendingId === run.runId}
                actionError={actionError}
                onStop={() => void handleStop(run.runId)}
                onDuplicate={handleDuplicate}
                deleteError={deleteError}
                onDelete={() => void handleDelete(run.runId)}
              />

              <RatingChart snapshots={dashboard?.snapshots ?? []} />

              <TabBar active={activeTab} onChange={setActiveTab} />

              <div className="tab-content">
                {activeTab === "tournament" && (
                  <TournamentTab
                    agents={run.leaderboard.agents}
                    bots={run.leaderboard.bots}
                    state={state}
                    totalGameCount={run.gameCount}
                    finished={displayStatus === "finished"}
                    settlementMode={config.settlement_mode}
                    finalScores={run.finalScores}
                    payouts={run.payouts}
                  />
                )}
                {activeTab === "marketplace" && <MarketplaceTab state={state} agentModels={agentModels} />}
                {activeTab === "agents" && (
                  <AgentsTab
                    runId={run.runId}
                    runStatus={displayStatus}
                    config={config}
                    state={state}
                    logAgentId={logAgentId}
                    onLogAgentIdChange={handleSetLogAgentId}
                    highlightFilter={highlightFilter}
                    onHighlightFilterChange={setHighlightFilter}
                  />
                )}
                {activeTab === "comments" && (
                  <CommentsTab
                    comments={commentFeedMessages}
                    authorFilter={commentAuthorFilter}
                    onAuthorFilterChange={setCommentAuthorFilter}
                    contentFilter={commentContentFilter}
                    onContentFilterChange={setCommentContentFilter}
                  />
                )}
                {activeTab === "events" && <EventsTab events={dashboard?.events ?? []} />}
              </div>
            </>
          ) : (
            <div className="panel empty-state">
              <h2>Select a run</h2>
              <p>Choose a run from the sidebar or create a new one to get started.</p>
            </div>
          )}
        </section>

        {run && (
          <LiveChatPane
            runName={run.name}
            messages={commentFeedMessages}
            open={chatOpen}
          />
        )}
      </div>

      <NewRunModal
        open={showNewRun}
        initialConfig={seedConfig}
        onClose={() => {
          setShowNewRun(false);
          setSeedConfig(null);
        }}
        onCreated={(runId) => {
          setSelectedRunId(runId);
          void loadRuns();
        }}
      />
    </main>
  );
}
