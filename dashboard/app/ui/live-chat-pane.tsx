"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { renderWithEmotes } from "./emotes";
import type { RunComment } from "./types";

function formatTime(ts: number) {
  const d = new Date(ts);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function preview(text: string) {
  return text.length > 80 ? `${text.slice(0, 80)}...` : text;
}

export function LiveChatPane({
  runName,
  messages,
  open,
}: {
  runName: string;
  messages: RunComment[];
  open?: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [authorFilter, setAuthorFilter] = useState<string>("all");
  const authors = useMemo(
    () => Array.from(new Set(messages.map((message) => message.author))).sort((left, right) => left.localeCompare(right)),
    [messages],
  );
  const recentMessages = useMemo(
    () =>
      messages
        .filter((message) => authorFilter === "all" || message.author === authorFilter)
        .slice(-50),
    [authorFilter, messages],
  );
  const byId = useMemo(
    () => Object.fromEntries(messages.map((m) => [m.commentId, m])),
    [messages],
  );

  useEffect(() => {
    if (!bodyRef.current) return;
    bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [recentMessages]);

  return (
    <aside className="chat-panel" data-open={String(open !== false)}>
      <div className="chat-panel__header">
        <h3>Stream Chat</h3>
        <p>{runName}</p>
        <label className="chat-panel__filter">
          <span>Agent</span>
          <select value={authorFilter} onChange={(event) => setAuthorFilter(event.target.value)}>
            <option value="all">All agents</option>
            {authors.map((author) => (
              <option key={author} value={author}>
                {author}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div ref={bodyRef} className="chat-panel__body">
        {recentMessages.length === 0 ? (
          <div className="empty">No messages yet.</div>
        ) : (
          recentMessages.map((msg) => {
            const parent = msg.parentMessageId ? byId[msg.parentMessageId] : undefined;
            return (
              <div key={msg.commentId} className="chat-msg">
                {parent && (
                  <span className="chat-msg__reply">
                    ↩ {parent.author}: {preview(parent.body)}
                  </span>
                )}
                <span className="chat-msg__author">{msg.author}:</span>
                <span className="chat-msg__text">{renderWithEmotes(msg.body)}</span>
                <span className="chat-msg__time">{formatTime(msg.createdAt)}</span>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
