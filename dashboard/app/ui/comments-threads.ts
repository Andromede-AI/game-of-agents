import { classifyCommentContent } from "./content-insights";
import type { CommentContentFilter, RunComment } from "./types";

export type ThreadNode = RunComment & {
  tags: string[];
  strategic: boolean;
  depth: number;
  matchesFilters: boolean;
  children: ThreadNode[];
};

export type ThreadFilters = {
  authorFilter: string;
  contentFilter: CommentContentFilter;
};

export function buildThreads(comments: RunComment[]): ThreadNode[] {
  const nodes = new Map<string, ThreadNode>();
  const parentToChildren = new Map<string, ThreadNode[]>();
  const roots: ThreadNode[] = [];

  for (const comment of comments) {
    const content = classifyCommentContent(comment.body);
    nodes.set(comment.commentId, {
      ...comment,
      tags: content.tags,
      strategic: content.strategic,
      depth: 0,
      matchesFilters: false,
      children: [],
    });
  }

  for (const node of nodes.values()) {
    const parentId = node.parentMessageId ?? null;
    if (!parentId || !nodes.has(parentId)) {
      roots.push(node);
      continue;
    }
    const siblings = parentToChildren.get(parentId) ?? [];
    siblings.push(node);
    parentToChildren.set(parentId, siblings);
  }

  const attachChildren = (node: ThreadNode, depth: number) => {
    node.depth = depth;
    const children = [...(parentToChildren.get(node.commentId) ?? [])].sort(
      (left, right) => left.createdAt - right.createdAt,
    );
    node.children = children;
    children.forEach((child) => attachChildren(child, depth + 1));
  };

  roots.sort((left, right) => left.createdAt - right.createdAt);
  roots.forEach((root) => attachChildren(root, 0));
  return roots;
}

export function flattenThreadNodes(nodes: ThreadNode[]): ThreadNode[] {
  return nodes.flatMap((node) => [node, ...flattenThreadNodes(node.children)]);
}

function matchesNodeFilters(node: ThreadNode, filters: ThreadFilters) {
  if (filters.authorFilter !== "all" && node.author !== filters.authorFilter) {
    return false;
  }
  if (filters.contentFilter === "strategic" && !node.strategic) {
    return false;
  }
  if (filters.contentFilter === "social" && node.strategic) {
    return false;
  }
  return true;
}

function filterNode(
  node: ThreadNode,
  filters: ThreadFilters,
  matchedNodes: ThreadNode[],
): ThreadNode | null {
  const children = node.children
    .map((child) => filterNode(child, filters, matchedNodes))
    .filter((child): child is ThreadNode => Boolean(child));
  const matchesFilters = matchesNodeFilters(node, filters);
  if (!matchesFilters && !children.length) {
    return null;
  }
  const nextNode: ThreadNode = {
    ...node,
    matchesFilters,
    children,
  };
  if (matchesFilters) {
    matchedNodes.push(nextNode);
  }
  return nextNode;
}

export function filterThreads(nodes: ThreadNode[], filters: ThreadFilters) {
  const matchedNodes: ThreadNode[] = [];
  const visibleRoots = nodes
    .map((node) => filterNode(node, filters, matchedNodes))
    .filter((node): node is ThreadNode => Boolean(node));
  const visibleNodes = flattenThreadNodes(visibleRoots);
  const visibleById = Object.fromEntries(visibleNodes.map((node) => [node.commentId, node]));
  const matchedById = Object.fromEntries(matchedNodes.map((node) => [node.commentId, node]));
  return {
    visibleRoots,
    visibleNodes,
    visibleById,
    matchedNodes,
    matchedById,
  };
}

export function buildCommentStats(
  matchedNodes: ThreadNode[],
  matchedById: Record<string, ThreadNode | undefined>,
) {
  const perAgent = new Map<string, number>();
  const replyPairs = new Map<string, number>();
  let strategicCount = 0;

  for (const node of matchedNodes) {
    perAgent.set(node.author, Number(perAgent.get(node.author) ?? 0) + 1);
    if (node.strategic) {
      strategicCount += 1;
    }
    const parent = node.parentMessageId ? matchedById[node.parentMessageId] : undefined;
    if (parent) {
      const key = `${node.author}→${parent.author}`;
      replyPairs.set(key, Number(replyPairs.get(key) ?? 0) + 1);
    }
  }

  return {
    messagesPerAgent: Array.from(perAgent.entries()).sort(
      (left, right) => right[1] - left[1] || left[0].localeCompare(right[0]),
    ),
    replyPatterns: Array.from(replyPairs.entries())
      .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
      .slice(0, 6),
    strategicCount,
    socialCount: Math.max(0, matchedNodes.length - strategicCount),
  };
}
