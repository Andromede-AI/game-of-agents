import test from "node:test";
import assert from "node:assert/strict";

import { buildCommentStats, buildThreads, filterThreads } from "./comments-threads";
import type { RunComment } from "./types";

const COMMENTS: RunComment[] = [
  {
    commentId: "root",
    createdAt: 1,
    author: "alpha",
    parentMessageId: null,
    source: "commentary",
    body: "hello everyone",
    offerId: null,
  },
  {
    commentId: "child",
    createdAt: 2,
    author: "beta",
    parentMessageId: "root",
    source: "commentary",
    body: "we should buy the strong bot offer",
    offerId: null,
  },
  {
    commentId: "grandchild",
    createdAt: 3,
    author: "gamma",
    parentMessageId: "child",
    source: "commentary",
    body: "agreed, that offer looks strong",
    offerId: null,
  },
];

test("thread filtering preserves ancestor context for matched replies", () => {
  const threads = buildThreads(COMMENTS);
  const filtered = filterThreads(threads, {
    authorFilter: "beta",
    contentFilter: "all",
  });

  assert.deepEqual(
    filtered.visibleNodes.map((node) => `${node.commentId}:${node.matchesFilters}`),
    ["root:false", "child:true"],
  );
});

test("comment stats only count directly matched messages and reply edges", () => {
  const threads = buildThreads(COMMENTS);
  const filtered = filterThreads(threads, {
    authorFilter: "all",
    contentFilter: "strategic",
  });
  const stats = buildCommentStats(filtered.matchedNodes, filtered.matchedById);

  assert.deepEqual(stats.messagesPerAgent, [
    ["beta", 1],
    ["gamma", 1],
  ]);
  assert.deepEqual(stats.replyPatterns, [["gamma→beta", 1]]);
  assert.equal(stats.strategicCount, 2);
  assert.equal(stats.socialCount, 0);
});
