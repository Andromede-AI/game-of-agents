import { forwardBackend } from "../../../_lib/backend";

export async function GET(
  request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const { runId } = await context.params;
  const url = new URL(request.url);
  const params = new URLSearchParams();
  for (const [key, value] of url.searchParams.entries()) {
    params.set(key, value);
  }
  const suffix = params.size ? `?${params.toString()}` : "";
  return forwardBackend(`/runs/${runId}/dashboard${suffix}`, { method: "GET" });
}
