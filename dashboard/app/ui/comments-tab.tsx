import { useMemo, useRef } from "react";

import { commentIdentity } from "./comment-feed";
import { buildCommentStats, buildThreads, filterThreads, type ThreadNode } from "./comments-threads";
import { colorForAgent } from "./content-insights";
import { downloadCsv } from "./csv";
import { renderWithEmotes } from "./emotes";
import { exportNodeAsPng } from "./export-utils";
import type { CommentContentFilter, RunComment } from "./types";

function formatTime(ts: number) {
  const d = new Date(ts);
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}

function preview(text: string) {
  return text.length > 120 ? `${text.slice(0, 120)}...` : text;
}

function ThreadEntry({
  node,
  byId,
}: {
  node: ThreadNode;
  byId: Record<string, ThreadNode>;
}) {
  const parent = node.parentMessageId ? byId[node.parentMessageId] : undefined;
  return (
    <article
      className="comment-thread"
      style={{
        borderLeftColor: colorForAgent(node.author),
        marginLeft: `${Math.min(node.depth, 4) * 18}px`,
      }}
      data-strategic={String(node.strategic)}
      data-context={String(!node.matchesFilters)}
    >
      <div className="comment-card__meta">
        <div className="comment-card__identity">
          <span className="comment-card__author" style={{ color: colorForAgent(node.author) }}>
            {node.author}
          </span>
          <span className="comment-card__handle">{commentIdentity(node)}</span>
        </div>
        <span className="comment-card__source">{node.source}</span>
        <time className="comment-card__time">{formatTime(node.createdAt)}</time>
      </div>

      {parent && (
        <div className="comment-card__reply">
          <span className="comment-card__reply-label">Replying to {parent.author}</span>
          <p>{preview(parent.body)}</p>
        </div>
      )}

      <div className="comment-thread__tags">
        <span className="comment-thread__tag" data-tag={node.strategic ? "strategic" : "social"}>
          {node.strategic ? "strategic" : "social"}
        </span>
        {!node.matchesFilters && (
          <span className="comment-thread__tag" data-tag="context">context</span>
        )}
        {node.tags.map((tag) => (
          <span key={`${node.commentId}-${tag}`} className="comment-thread__tag" data-tag={tag}>
            {tag}
          </span>
        ))}
      </div>

      <p className="comment-card__body">{renderWithEmotes(node.body)}</p>

      {node.offerId && <div className="comment-card__offer">Offer context: {node.offerId}</div>}
    </article>
  );
}

export function CommentsTab({
  comments,
  authorFilter,
  onAuthorFilterChange,
  contentFilter,
  onContentFilterChange,
}: {
  comments: RunComment[];
  authorFilter: string;
  onAuthorFilterChange: (value: string) => void;
  contentFilter: CommentContentFilter;
  onContentFilterChange: (value: CommentContentFilter) => void;
}) {
  const summaryRef = useRef<HTMLElement | null>(null);

  const authors = useMemo(
    () => Array.from(new Set(comments.map((comment) => comment.author))).sort((left, right) => left.localeCompare(right)),
    [comments],
  );

  const threads = useMemo(() => buildThreads(comments), [comments]);
  const filtered = useMemo(
    () =>
      filterThreads(threads, {
        authorFilter,
        contentFilter,
      }),
    [authorFilter, contentFilter, threads],
  );
  const stats = useMemo(
    () => buildCommentStats(filtered.matchedNodes, filtered.matchedById),
    [filtered.matchedById, filtered.matchedNodes],
  );

  const handleExportSummary = async () => {
    if (!summaryRef.current) {
      return;
    }
    await exportNodeAsPng(summaryRef.current, "comments-summary.png", "Commentary Summary");
  };

  const handleExportCsv = () => {
    downloadCsv(
      "comments.csv",
      threads.flatMap((root) => {
        const stack = [root];
        const rows: Array<Record<string, string | number | boolean | null>> = [];
        while (stack.length > 0) {
          const node = stack.shift();
          if (!node) {
            continue;
          }
          const matchesAuthorFilter = authorFilter === "all" || node.author === authorFilter;
          const matchesContentFilter =
            contentFilter === "all" ||
            (contentFilter === "strategic" && node.strategic) ||
            (contentFilter === "social" && !node.strategic);
          rows.push({
            messageId: node.commentId,
            parentMessageId: node.parentMessageId ?? null,
            agentId: node.author,
            source: node.source,
            createdAt: node.createdAt,
            depth: node.depth,
            classification: node.strategic ? "strategic" : "social",
            tags: node.tags.join("|"),
            offerId: node.offerId ?? null,
            body: node.body,
            matchesAuthorFilter,
            matchesContentFilter,
            visibleInFilteredThread: Boolean(filtered.visibleById[node.commentId]),
          });
          stack.unshift(...node.children);
        }
        return rows;
      }),
    );
  };

  if (!comments.length) {
    return <div className="empty">No commentator messages have landed yet.</div>;
  }

  return (
    <div className="comments-feed comments-feed--threaded">
      <section className="comments-feed__summary panel" ref={summaryRef}>
        <div className="comments-feed__filters">
          <label>
            Agent
            <select value={authorFilter} onChange={(event) => onAuthorFilterChange(event.target.value)}>
              <option value="all">All agents</option>
              {authors.map((author) => (
                <option key={author} value={author}>
                  {author}
                </option>
              ))}
            </select>
          </label>
          <label>
            Content
            <select value={contentFilter} onChange={(event) => onContentFilterChange(event.target.value as CommentContentFilter)}>
              <option value="all">All</option>
              <option value="strategic">Strategic</option>
              <option value="social">Social</option>
            </select>
          </label>
          <span className="comments-feed__count">
            {filtered.matchedNodes.length} matching · {filtered.visibleNodes.length} in thread context
          </span>
          <div className="comments-feed__actions" data-export-ignore="true">
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => {
                onAuthorFilterChange("all");
                onContentFilterChange("all");
              }}
            >
              Reset Filters
            </button>
            <button type="button" className="btn btn--sm" onClick={() => void handleExportSummary()}>
              Export PNG
            </button>
            <button type="button" className="btn btn--sm" onClick={handleExportCsv}>
              Export CSV
            </button>
          </div>
        </div>

        <section className="comments-feed__stats">
          <div className="comments-feed__stat-card panel">
            <span>Strategic</span>
            <strong>{stats.strategicCount}</strong>
            <small>Social {stats.socialCount}</small>
          </div>
          <div className="comments-feed__stat-card panel">
            <span>Top speakers</span>
            <strong>{stats.messagesPerAgent[0]?.[0] ?? "—"}</strong>
            <small>{stats.messagesPerAgent[0]?.[1] ?? 0} messages</small>
          </div>
          <div className="comments-feed__stat-card panel">
            <span>Top reply pattern</span>
            <strong>{stats.replyPatterns[0]?.[0] ?? "—"}</strong>
            <small>{stats.replyPatterns[0]?.[1] ?? 0} replies</small>
          </div>
        </section>

        <div className="comments-feed__sidebar comments-feed__sidebar--summary">
          <div className="comments-feed__mini panel">
            <h4>Messages per Agent</h4>
            {stats.messagesPerAgent.length > 0 ? (
              stats.messagesPerAgent.map(([author, count]) => (
                <div key={author} className="comments-feed__mini-row">
                  <span style={{ color: colorForAgent(author) }}>{author}</span>
                  <span>{count}</span>
                </div>
              ))
            ) : (
              <div className="empty">No matching messages.</div>
            )}
          </div>

          <div className="comments-feed__mini panel">
            <h4>Reply Patterns</h4>
            {stats.replyPatterns.length > 0 ? (
              stats.replyPatterns.map(([pair, count]) => (
                <div key={pair} className="comments-feed__mini-row">
                  <span>{pair}</span>
                  <span>{count}</span>
                </div>
              ))
            ) : (
              <div className="empty">No matched reply pairs.</div>
            )}
          </div>
        </div>
      </section>

      <section className="comments-feed__details">
        <div className="comments-feed__list panel">
          {!filtered.visibleNodes.length ? <div className="empty">No messages match the current filters.</div> : null}
          {filtered.visibleNodes.map((node) => (
            <ThreadEntry key={node.commentId} node={node} byId={filtered.visibleById} />
          ))}
        </div>
      </section>
    </div>
  );
}
