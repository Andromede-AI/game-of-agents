import { forwardBackend } from "../../../_lib/backend";

export async function POST(
  _request: Request,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const { runId } = await context.params;
  return forwardBackend(`/runs/${runId}/stop`, {
    method: "POST",
  });
}
