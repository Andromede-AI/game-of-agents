import { forwardBackend } from "../../../../../_lib/backend";

type Params = Promise<{ runId: string; agentId: string }>;

export async function GET(
  request: Request,
  { params }: { params: Params },
) {
  const { runId, agentId } = await params;
  const url = new URL(request.url);
  const limit = url.searchParams.get("limit");
  const suffix = limit ? `?limit=${encodeURIComponent(limit)}` : "";
  return forwardBackend(`/runs/${runId}/agents/${agentId}/conversation${suffix}`);
}
