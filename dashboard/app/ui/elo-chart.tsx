"use client";

import { useEffect, useMemo, useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import type { Snapshot } from "./types";
import { useRatingSeries } from "./use-elo-series";

const COLORS = [
  "var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)",
  "var(--chart-5)", "var(--chart-6)", "var(--chart-7)", "var(--chart-8)",
];

function formatTime(ts: number, includeSeconds = false) {
  const d = new Date(ts);
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (!includeSeconds) {
    return hhmm;
  }
  return `${hhmm}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function HoverTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean;
  label?: number | string;
  payload?: Array<{ color?: string; name?: string; value?: number | string | null }>;
}) {
  if (!active || label === undefined || !payload?.length) {
    return null;
  }
  const rows = [...payload]
    .filter((entry) => typeof entry.value === "number" && Number.isFinite(entry.value))
    .sort((left, right) => Number(right.value) - Number(left.value));
  if (!rows.length) {
    return null;
  }
  return (
    <div className="elo-chart__tooltip">
      <div className="elo-chart__tooltip-label">{formatTime(Number(label), true)}</div>
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
            <span className="elo-chart__tooltip-value">{Number(entry.value).toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function RatingChart({ snapshots }: { snapshots: Snapshot[] }) {
  const [mode, setMode] = useState<"agents" | "bots">("agents");
  const { data, entities } = useRatingSeries(snapshots, mode);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [botAgentFilter, setBotAgentFilter] = useState<string>("all");

  useEffect(() => {
    if (mode === "agents") {
      setBotAgentFilter("all");
    }
  }, [mode]);

  function toggleEntity(id: string) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const botGroups = useMemo(() => {
    const groups = new Map<string, typeof entities>();
    for (const entity of entities) {
      const agentId = entity.agentId ?? "ungrouped";
      const existing = groups.get(agentId) ?? [];
      existing.push(entity);
      groups.set(agentId, existing);
    }
    return Array.from(groups.entries()).sort(([left], [right]) => left.localeCompare(right));
  }, [entities]);

  const visible = entities.filter((entity) => {
    if (hidden.has(entity.id)) {
      return false;
    }
    if (mode !== "bots" || botAgentFilter === "all") {
      return true;
    }
    return entity.agentId === botAgentFilter;
  });
  const showSeconds = data.length > 1 && data[data.length - 1].time - data[0].time < 15 * 60 * 1000;
  const visibleIds = useMemo(() => visible.map((entity) => entity.id), [visible]);
  const colorByEntityId = useMemo(
    () => new Map(entities.map((entity, index) => [entity.id, COLORS[index % COLORS.length]])),
    [entities],
  );

  const [yMin, yMax] = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const point of data) {
      for (const id of visibleIds) {
        const v = point[id];
        if (typeof v === "number" && Number.isFinite(v)) {
          if (v < lo) lo = v;
          if (v > hi) hi = v;
        }
      }
    }
    if (!Number.isFinite(lo)) return [undefined, undefined];
    const range = hi - lo || 1;
    const padding = range * 0.1;
    return [Math.floor(lo - padding), Math.ceil(hi + padding)];
  }, [data, visibleIds]);

  return (
    <section className="elo-chart">
      <div className="elo-chart__header">
        <h2>Rating Over Time</h2>
        <div className="elo-chart__mode">
          <button
            type="button"
            className="btn btn--sm"
            data-active={String(mode === "agents")}
            onClick={() => setMode("agents")}
          >
            By Agent
          </button>
          <button
            type="button"
            className="btn btn--sm"
            data-active={String(mode === "bots")}
            onClick={() => setMode("bots")}
          >
            By Bot
          </button>
        </div>
      </div>

      {mode === "bots" && botGroups.length > 1 && (
        <div className="elo-chart__filters">
          <label className="elo-chart__filter">
            <span>Agent</span>
            <select value={botAgentFilter} onChange={(event) => setBotAgentFilter(event.target.value)}>
              <option value="all">All agents</option>
              {botGroups.map(([agentId]) => (
                <option key={agentId} value={agentId}>
                  {agentId}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {mode === "bots" ? (
        <div className="elo-chart__chip-groups">
          {botGroups
            .filter(([agentId]) => botAgentFilter === "all" || agentId === botAgentFilter)
            .map(([agentId, group]) => (
              <section key={agentId} className="elo-chart__chip-group">
                {botAgentFilter === "all" && <h3 className="elo-chart__chip-group-label">{agentId}</h3>}
                <div className="elo-chart__chips">
                  {group.map((entity) => {
                    return (
                      <button
                        key={entity.id}
                        type="button"
                        className="chip"
                        data-active={String(!hidden.has(entity.id))}
                        onClick={() => toggleEntity(entity.id)}
                      >
                        <span
                          className="chip__dot"
                          style={{ background: colorByEntityId.get(entity.id) ?? COLORS[0] }}
                        />
                        {entity.label}
                      </button>
                    );
                  })}
                </div>
              </section>
            ))}
        </div>
      ) : (
        <div className="elo-chart__chips">
          {entities.map((entity, i) => (
            <button
              key={entity.id}
              type="button"
              className="chip"
              data-active={String(!hidden.has(entity.id))}
              onClick={() => toggleEntity(entity.id)}
            >
              <span
                className="chip__dot"
                style={{ background: COLORS[i % COLORS.length] }}
              />
              {entity.label}
            </button>
          ))}
        </div>
      )}

      <div className="elo-chart__body">
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height="100%" debounce={150}>
            <LineChart data={data}>
              <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
              <XAxis
                dataKey="time"
                tickFormatter={(value) => formatTime(Number(value), showSeconds)}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "#848494", fontSize: 11 }}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tick={{ fill: "#848494", fontSize: 11 }}
                domain={yMin !== undefined ? [yMin, yMax] : ["auto", "auto"]}
              />
              <Tooltip
                content={<HoverTooltip />}
              />
              {visible.map((entity) => {
                return (
                  <Line
                    key={entity.id}
                    type="linear"
                    dataKey={entity.id}
                    stroke={colorByEntityId.get(entity.id) ?? COLORS[0]}
                    strokeWidth={2.2}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls={false}
                    name={entity.label}
                  />
                );
              })}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="empty">No snapshot data yet.</div>
        )}
      </div>
    </section>
  );
}
