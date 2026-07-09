import { forwardBackend } from "../../../../../_lib/backend";

export async function POST(
  request: Request,
  context: { params: Promise<{ runId: string; agentId: string }> },
): Promise<Response> {
  const { runId, agentId } = await context.params;
  return forwardBackend(`/runs/${runId}/agents/${agentId}/steer`, {
    method: "POST",
    body: await request.text(),
    headers: {
      "Content-Type": request.headers.get("content-type") ?? "application/json",
    },
  });
}
