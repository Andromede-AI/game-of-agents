"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AgentConfig,
  AgentConversationBlock,
  AgentState,
  RunConfig,
  RunState,
} from "./types";
import { classifyTranscriptHighlight, type HighlightCategory } from "./content-insights";
import { exportNodeAsPng } from "./export-utils";

function downloadText(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function AgentsTab({
  runId,
  runStatus,
  config,
  state,
  logAgentId,
  onLogAgentIdChange,
  highlightFilter,
  onHighlightFilterChange,
}: {
  runId: string;
  runStatus: string;
  config: RunConfig;
  state: RunState;
  logAgentId: string | null;
  onLogAgentIdChange: (agentId: string | null) => void;
  highlightFilter: "all" | HighlightCategory;
  onHighlightFilterChange: (value: "all" | HighlightCategory) => void;
}) {
  const agentConfigs = config.agents ?? [];
  const [conversation, setConversation] = useState<AgentConversationBlock[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [steerText, setSteerText] = useState("");
  const [steerPending, setSteerPending] = useState(false);
  const [steerError, setSteerError] = useState<string | null>(null);
  const [steerSuccess, setSteerSuccess] = useState<string | null>(null);
  const highlightsRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!logAgentId) {
      setConversation([]);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    let firstLoad = true;
    setSteerText("");
    setSteerError(null);
    setSteerSuccess(null);

    const loadConversation = async () => {
      if (firstLoad) {
        setLoading(true);
      }
      try {
        const response = await fetch(
          `/api/runs/${runId}/agents/${logAgentId}/conversation?limit=600`,
          { cache: "no-store" },
        );
        if (!response.ok) {
          throw new Error(`conversation fetch failed (${response.status})`);
        }
        const blocks = (await response.json()) as AgentConversationBlock[];
        if (!cancelled) {
          setConversation(blocks);
          setError(null);
        }
      } catch (fetchError) {
        if (!cancelled) {
          setError(fetchError instanceof Error ? fetchError.message : "Conversation fetch failed");
        }
      } finally {
        firstLoad = false;
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadConversation();
    const interval = window.setInterval(loadConversation, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [logAgentId, runId]);

  const fullOutputs = useMemo(
    () => conversation.filter((block) => block.kind === "text" || block.kind === "tool"),
    [conversation],
  );
  const highlights = useMemo(
    () =>
      conversation
        .map((block) => {
          const category = classifyTranscriptHighlight(block.text);
          if (!category) {
            return null;
          }
          return {
            blockId: block.block_id,
            category,
            title: block.title,
            createdAt: block.created_at,
            text: block.text.trim(),
          };
        })
        .filter((item): item is { blockId: string; category: HighlightCategory; title: string; createdAt: string | number; text: string } => Boolean(item))
        .filter((item) => highlightFilter === "all" || item.category === highlightFilter),
    [conversation, highlightFilter],
  );

  const handleDownloadConversation = () => {
    if (!logAgentId || !conversation.length) {
      return;
    }
    const text = conversation
      .map((block) => {
        const startedAt = formatTime(block.created_at);
        const streaming = block.streaming ? " (streaming)" : "";
        return `[${startedAt}] ${block.title}${streaming}\n${block.text}\n`;
      })
      .join("\n");
    downloadText(`${logAgentId}-conversation.txt`, text);
  };

  const handleExportHighlights = async () => {
    if (!highlightsRef.current || !logAgentId) {
      return;
    }
    await exportNodeAsPng(highlightsRef.current, `${logAgentId}-highlights.png`, `Agent Highlights: ${logAgentId}`);
  };

  const handleSteer = async () => {
    if (!logAgentId || !steerText.trim()) {
      return;
    }
    setSteerPending(true);
    setSteerError(null);
    setSteerSuccess(null);
    try {
      const response = await fetch(`/api/runs/${runId}/agents/${logAgentId}/steer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: steerText.trim() }),
      });
      if (!response.ok) {
        let detail = `Steer failed with status ${response.status}.`;
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
      setSteerSuccess("Advice queued for the next continue step.");
      setSteerText("");
    } catch (steerActionError) {
      setSteerError(steerActionError instanceof Error ? steerActionError.message : "Steer failed.");
    } finally {
      setSteerPending(false);
    }
  };

  if (!agentConfigs.length) {
    return <div className="empty">No agents configured.</div>;
  }

  return (
    <>
      <div className="agents-grid">
        {agentConfigs.map((agentConfig) => {
          const agentState = (state.agents ?? {})[agentConfig.agent_id] as AgentState | undefined;
          return (
            <AgentCard
              key={agentConfig.agent_id}
              config={agentConfig}
              state={agentState}
              onViewLog={() => onLogAgentIdChange(logAgentId === agentConfig.agent_id ? null : agentConfig.agent_id)}
              logOpen={logAgentId === agentConfig.agent_id}
            />
          );
        })}
      </div>

      {logAgentId && (
        <div className="agent-log panel">
          <div className="agent-log__header">
            <h3>Conversation: {logAgentId}</h3>
            <div className="agent-log__actions">
              {fullOutputs.length > 0 && (
                <button type="button" className="btn btn--sm btn--accent" onClick={handleDownloadConversation}>
                  Download
                </button>
              )}
              <button type="button" className="btn btn--sm" onClick={() => onLogAgentIdChange(null)}>
                Close
              </button>
            </div>
          </div>
          <div className="agent-log__steer">
            <label className="agent-log__steer-label" htmlFor="agent-steer">
              Operator Advice
            </label>
            <textarea
              id="agent-steer"
              className="agent-log__steer-input"
              value={steerText}
              onChange={(event) => setSteerText(event.target.value)}
              placeholder="Give this agent a short steer for the next continue prompt."
              maxLength={600}
              disabled={runStatus !== "running" || steerPending}
            />
            <div className="agent-log__steer-actions">
              <span className="agent-log__steer-hint">
                Queued advice is wrapped into the next continue prompt.
              </span>
              <button
                type="button"
                className="btn btn--sm btn--accent"
                onClick={() => void handleSteer()}
                disabled={runStatus !== "running" || steerPending || !steerText.trim()}
              >
                {steerPending ? "Sending…" : "Send Steer"}
              </button>
            </div>
            {steerError && <div className="agent-log__steer-error">{steerError}</div>}
            {steerSuccess && <div className="agent-log__steer-success">{steerSuccess}</div>}
          </div>
          <div className="agent-highlights panel" ref={highlightsRef}>
            <div className="agent-highlights__header">
              <div>
                <h4>Highlights</h4>
                <p>Keyword-derived moments for marketplace, strategy, competition, and chat decisions.</p>
              </div>
              <div className="agent-highlights__actions" data-export-ignore="true">
                <div className="elo-chart__chips">
                  <button type="button" className="chip" data-active={String(highlightFilter === "all")} onClick={() => onHighlightFilterChange("all")}>
                    All
                  </button>
                  {(["marketplace", "strategy", "competition", "chat"] as HighlightCategory[]).map((category) => (
                    <button
                      key={category}
                      type="button"
                      className="chip"
                      data-active={String(highlightFilter === category)}
                      onClick={() => onHighlightFilterChange(category)}
                    >
                      {category}
                    </button>
                  ))}
                </div>
                <button type="button" className="btn btn--sm" onClick={() => void handleExportHighlights()}>
                  Export PNG
                </button>
              </div>
            </div>
            <div className="agent-highlights__body">
              {highlights.length > 0 ? (
                highlights.map((item) => (
                  <article key={item.blockId} className="agent-highlights__card" data-category={item.category}>
                    <div className="agent-highlights__meta">
                      <span>{formatTime(item.createdAt)}</span>
                      <span className="agent-highlights__badge">{item.category}</span>
                      <strong>{item.title}</strong>
                    </div>
                    <p>{item.text.length > 300 ? `${item.text.slice(0, 300)}...` : item.text}</p>
                  </article>
                ))
              ) : (
                <div className="empty">No highlights match the current filter.</div>
              )}
            </div>
          </div>
          <div className="agent-log__body">
            {loading && <div className="empty">Loading conversation…</div>}
            {!loading && error && <div className="empty">{error}</div>}
            {!loading && !error && conversation.length === 0 && (
              <div className="empty">No conversation blocks for this agent yet.</div>
            )}
            {!loading &&
              !error &&
              conversation.map((block) => (
                <ConversationBlockView key={block.block_id} block={block} />
              ))}
          </div>
        </div>
      )}
    </>
  );
}

function ConversationBlockView({ block }: { block: AgentConversationBlock }) {
  const [open, setOpen] = useState(!block.collapsed);

  useEffect(() => {
    if (!block.collapsed) {
      setOpen(true);
    }
  }, [block.block_id, block.collapsed]);

  return (
    <div className="agent-log__entry agent-log__entry--conversation" data-kind={block.kind}>
      <div className="agent-log__meta">
        <span className="agent-log__time">{formatTime(block.created_at)}</span>
        <span className="agent-log__kind" data-kind={`agent.${block.kind}`}>
          {block.title}
        </span>
        <span className="agent-log__role">{block.role}</span>
        <button type="button" className="btn btn--sm" onClick={() => setOpen((value) => !value)}>
          {open ? "Collapse" : "Expand"}
        </button>
        {block.streaming && <span className="agent-log__streaming">live</span>}
      </div>
      {open ? (
        <pre className="agent-log__message">{block.text || "(empty)"}</pre>
      ) : (
        <pre className="agent-log__message agent-log__message--truncated">
          {preview(block.text)}
        </pre>
      )}
    </div>
  );
}

function formatTime(value: string | number) {
  const time = typeof value === "number" ? value : Date.parse(value);
  const date = new Date(time);
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((item) => String(item).padStart(2, "0"))
    .join(":");
}

function preview(text: string) {
  if (text.length <= 200) {
    return text;
  }
  return `${text.slice(0, 200)}...`;
}

function AgentCard({
  config,
  state,
  onViewLog,
  logOpen,
}: {
  config: AgentConfig;
  state: AgentState | undefined;
  onViewLog: () => void;
  logOpen: boolean;
}) {
  return (
    <div className="agent-card panel">
      <div className="agent-card__header">
        <strong>{config.agent_id}</strong>
        <span className="status-pill">
          <span className="dot" data-status={state?.status ?? "unknown"} />
          {state?.status ?? "unknown"}
        </span>
      </div>

      <dl className="agent-card__info">
        {config.model && (
          <div>
            <dt>Model</dt>
            <dd>{config.model}</dd>
          </div>
        )}
        <div>
          <dt>Runtime</dt>
          <dd>{config.runtime}</dd>
        </div>
        <div>
          <dt>Internet</dt>
          <dd>{config.internet_access ? "Yes" : "No"}</dd>
        </div>
        {state && (
          <>
            <div>
              <dt>Best Bot</dt>
              <dd>{state.best_bot_id ?? "—"}</dd>
            </div>
            <div>
              <dt>Best Rating</dt>
              <dd>{((state.best_rating_score ?? state.best_rating ?? state.best_elo ?? 0) as number).toFixed(1)}</dd>
            </div>
          </>
        )}
      </dl>

      <blockquote className="agent-card__prompt">
        <strong>Personality:</strong> {config.prompt}
      </blockquote>

      <code className="agent-card__cmd">{(config.command ?? []).join(" ") || "default runtime command"}</code>

      {state?.last_message && (
        <div className="agent-card__last-msg">
          <strong>Last summary</strong>
          <pre>{state.last_message}</pre>
        </div>
      )}

      <button type="button" className="btn btn--sm" data-active={String(logOpen)} onClick={onViewLog}>
        {logOpen ? "Hide Conversation" : "View Conversation"}
      </button>
    </div>
  );
}
