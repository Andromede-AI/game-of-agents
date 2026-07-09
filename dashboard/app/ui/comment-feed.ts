import type { RunComment } from "./types";

const SYNTHETIC_SOURCES = new Set([
  "agent note",
  "agent failure",
  "controller message",
  "time warning",
  "run",
  "run failure",
  "run result",
  "marketplace review",
]);

function normalizedSource(comment: RunComment) {
  return (comment.source ?? "").trim().toLowerCase();
}

export function isRealCommentFeedMessage(comment: RunComment) {
  if (comment.commentatorId) {
    return true;
  }
  if (comment.parentMessageId && !SYNTHETIC_SOURCES.has(normalizedSource(comment))) {
    return true;
  }
  const source = normalizedSource(comment);
  return source.includes("comment") || source.includes("commentary");
}

export function buildCommentFeed(comments: RunComment[], feedMessages: RunComment[]) {
  const merged = [...feedMessages, ...comments.filter(isRealCommentFeedMessage)];
  const deduped = new Map<string, RunComment>();

  for (const comment of merged) {
    if (!isRealCommentFeedMessage(comment)) {
      continue;
    }
    deduped.set(comment.commentId, comment);
  }

  return Array.from(deduped.values()).sort((left, right) => left.createdAt - right.createdAt);
}

export function commentIdentity(comment: RunComment) {
  return comment.commentatorId ?? comment.author;
}
