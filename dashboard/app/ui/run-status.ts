export function effectiveRunStatus(
  status: string | null | undefined,
  finishedAt: number | string | null | undefined,
): string {
  if (status === "failed") {
    return "failed";
  }
  if (finishedAt !== null && finishedAt !== undefined) {
    return "finished";
  }
  return status ?? "pending";
}

export function isActiveRunStatus(status: string): boolean {
  return status === "running" || status === "stopping";
}
