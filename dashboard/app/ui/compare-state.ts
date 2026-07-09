import type { CompareResourceState } from "./types";

export function needsCompareResourceFetch<T>(
  resource: CompareResourceState<T> | undefined,
  runUpdatedAt: number,
) {
  if (!resource) {
    return true;
  }
  if (resource.status === "loading") {
    return false;
  }
  return (resource.sourceUpdatedAt ?? 0) < runUpdatedAt;
}

export function beginCompareResourceFetch<T>(
  current: CompareResourceState<T> | undefined,
  sourceUpdatedAt: number,
): CompareResourceState<T> {
  return {
    status: "loading",
    data: current?.data,
    error: null,
    sourceUpdatedAt,
  };
}

export function succeedCompareResourceFetch<T>(
  data: T,
  sourceUpdatedAt: number,
): CompareResourceState<T> {
  return {
    status: "success",
    data,
    error: null,
    sourceUpdatedAt,
  };
}

export function failCompareResourceFetch<T>(
  current: CompareResourceState<T> | undefined,
  error: string,
  sourceUpdatedAt: number,
): CompareResourceState<T> {
  return {
    status: "error",
    data: current?.data,
    error,
    sourceUpdatedAt,
  };
}

export function pruneCompareResourceMap<T>(
  current: Record<string, CompareResourceState<T> | undefined>,
  validRunIds: Set<string>,
) {
  const next: Record<string, CompareResourceState<T> | undefined> = {};
  for (const [runId, resource] of Object.entries(current)) {
    if (validRunIds.has(runId)) {
      next[runId] = resource;
    }
  }
  return next;
}
