import { forwardBackend } from "../_lib/backend";

export async function GET(): Promise<Response> {
  return forwardBackend("/default-run-config");
}
