import type { RunEvent } from "./types";

function formatTime(ts: number) {
  const d = new Date(ts);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function formatEvent(kind: string, payload: Record<string, unknown>): string {
  switch (kind) {
    case "bot.submitted":
      return `${payload.agent_id ?? "?"} submitted bot '${payload.name ?? payload.bot_name ?? payload.bot_id ?? "?"}'`;
    case "game.finished":
      return `${payload.bot_a ?? "?"} vs ${payload.bot_b ?? "?"} → ${payload.winner_bot_id ?? "draw"} (${payload.reason ?? "?"})`;
    case "game.started":
      return `Game started: ${payload.bot_a ?? "?"} vs ${payload.bot_b ?? "?"}`;
    case "offer.created":
      return `${payload.agent_id ?? "?"} listed '${payload.title ?? "?"}' for ${payload.price_pct ?? "?"}%`;
    case "offer.purchased":
      return `${payload.buyer_agent_id ?? "?"} bought from ${payload.seller_agent_id ?? payload.agent_id ?? "?"} for ${payload.price_pct ?? "?"}%`;
    case "offer.reviewed":
      return `${payload.buyer_agent_id ?? "?"} reviewed: '${String(payload.text ?? "").slice(0, 80)}'`;
    case "bot.retired":
      return `${payload.agent_id ?? "?"} retired bot '${payload.bot_name ?? payload.bot_id ?? "?"}'`;
    case "run.started":
      return "Tournament started";
    case "run.finished":
      return "Tournament finished";
    case "run.warning":
      return `Warning: ${payload.message ?? "time running low"}`;
    case "agent.output":
      return `${payload.agent_id ?? "?"} full output (${String(payload.output ?? "").length} chars)`;
    default:
      return JSON.stringify(payload).slice(0, 120);
  }
}

const KIND_COLORS: Record<string, string> = {
  "bot.submitted": "var(--chart-2)",
  "game.finished": "var(--chart-1)",
  "game.started": "var(--chart-3)",
  "offer.created": "var(--chart-4)",
  "offer.purchased": "var(--chart-5)",
  "offer.reviewed": "var(--chart-6)",
  "bot.retired": "var(--muted)",
  "run.started": "var(--success)",
  "run.finished": "var(--success)",
  "run.warning": "var(--warning)",
  "agent.output": "var(--chart-7)",
};

export function EventsTab({ events }: { events: RunEvent[] }) {
  const sorted = [...events].reverse();

  if (!sorted.length) {
    return <div className="empty">No events recorded yet.</div>;
  }

  return (
    <div className="events-feed">
      {sorted.map((event) => (
        <div key={event.eventId} className="event-row">
          <span className="event-row__time">{formatTime(event.createdAt)}</span>
          <span
            className="event-row__kind"
            style={{ background: KIND_COLORS[event.kind] ?? "var(--muted)", color: "#fff" }}
          >
            {event.kind}
          </span>
          <span className="event-row__text">
            {formatEvent(event.kind, event.payload)}
          </span>
        </div>
      ))}
    </div>
  );
}
