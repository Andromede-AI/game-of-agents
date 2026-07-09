import { fetchAllBackends, forwardBackend } from "../_lib/backend";

export async function GET(): Promise<Response> {
  // Merge runs from all configured backends
  return fetchAllBackends("/runs");
}

export async function POST(request: Request): Promise<Response> {
  return forwardBackend("/runs", {
    method: "POST",
    body: await request.text(),
  });
}
