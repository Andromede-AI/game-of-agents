import "server-only";
import {
  fetchConvexRunDashboard,
  fetchConvexRuns,
  isConvexUrl,
} from "./convex-backend";

// Default backends — production Modal endpoint plus any additional ones
// configured via API_URLS env var (comma-separated).
//
// Backend URLs ending in `.convex.cloud` are queried directly via the Convex
// HTTP client (using `runs:listRuns` / `runs:getRunDashboard`). All other URLs
// are treated as Modal-style HTTP API servers and proxied with the API_TOKEN
// Bearer header.
const DEFAULT_BACKENDS = [
  "http://localhost:8000",
];

function trim(value: string | undefined): string | null {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

/**
 * Get all configured backends. Reads API_URLS (comma-separated) or falls back
 * to API_URL (single) or the default. Backends are tried in order.
 */
export function getBackendUrls(): string[] {
  const urls = trim(process.env.API_URLS);
  if (urls) {
    return urls.split(",").map((u) => u.trim()).filter(Boolean);
  }
  const single = trim(process.env.API_URL) ?? trim(process.env.NEXT_PUBLIC_API_URL);
  if (single) {
    return [single];
  }
  return DEFAULT_BACKENDS;
}

/** Backwards-compat: returns the first backend. */
export function getBackendUrl(): string {
  return getBackendUrls()[0];
}

function getBackendToken(): string {
  const token =
    trim(process.env.API_TOKEN) ?? trim(process.env.NEXT_PUBLIC_API_TOKEN);
  if (!token) {
    throw new Error("API_TOKEN is not configured for the dashboard server.");
  }
  return token;
}

/** Build a URL by string-joining the backend prefix with the path. */
function buildUrl(backend: string, path: string): string {
  const cleanBackend = backend.replace(/\/$/, "");
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  return `${cleanBackend}${cleanPath}`;
}

/**
 * Parse a path like `/runs/<id>/dashboard?sample_full_history=true&...`
 * Returns { runId, dashboardOpts } if matched, otherwise null.
 */
function matchRunDashboardPath(
  path: string,
): { runId: string; opts: { sampleFullHistory?: boolean; sampleSize?: number; eventLimit?: number } } | null {
  const [base, query = ""] = path.split("?");
  const m = base.match(/^\/runs\/([^/]+)\/dashboard$/);
  if (!m) return null;
  const params = new URLSearchParams(query);
  const opts: { sampleFullHistory?: boolean; sampleSize?: number; eventLimit?: number } = {};
  if (params.get("sample_full_history") === "true") opts.sampleFullHistory = true;
  const ss = params.get("sample_size");
  if (ss) opts.sampleSize = Number(ss);
  const el = params.get("event_limit");
  if (el) opts.eventLimit = Number(el);
  return { runId: decodeURIComponent(m[1]), opts };
}

/**
 * Dispatch a single backend request: HTTP fetch for Modal-style backends,
 * Convex client for *.convex.cloud backends.
 *
 * Returns null if the request type isn't supported by Convex backends
 * (e.g. POST/DELETE for now). Returns a Response otherwise.
 */
async function dispatchBackend(
  backend: string,
  path: string,
  init: RequestInit,
  headers: Headers,
): Promise<Response | null> {
  if (isConvexUrl(backend)) {
    // Only GET requests are supported via Convex direct
    const method = (init.method ?? "GET").toUpperCase();
    if (method !== "GET") return null;
    const dashMatch = matchRunDashboardPath(path);
    if (dashMatch) {
      return await fetchConvexRunDashboard(backend, dashMatch.runId, dashMatch.opts);
    }
    // Other paths (analysis, stop, etc.) aren't implemented for Convex direct
    return null;
  }

  const url = buildUrl(backend, path);
  return await fetch(url, {
    ...init,
    headers,
    cache: "no-store",
  });
}

/**
 * Try each backend in order until one returns a 2xx response.
 * Useful for run-detail requests where we don't know which backend has the run.
 */
export async function proxyBackend(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const backends = getBackendUrls();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${getBackendToken()}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let lastError: Response | null = null;
  for (const backend of backends) {
    try {
      const response = await dispatchBackend(backend, path, init, headers);
      if (!response) continue; // Convex backend doesn't support this path
      if (response.ok) {
        return response;
      }
      lastError = response;
    } catch {
      // try next backend
    }
  }
  return (
    lastError ??
    new Response(JSON.stringify({ detail: "All backends failed" }), {
      status: 502,
      headers: { "content-type": "application/json" },
    })
  );
}

export async function forwardBackend(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  try {
    const response = await proxyBackend(path, init);
    const body = await response.text();
    const headers = new Headers();
    const contentType = response.headers.get("content-type");
    if (contentType) {
      headers.set("content-type", contentType);
    }
    return new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unknown backend proxy error";
    return Response.json({ detail: message }, { status: 500 });
  }
}

/**
 * Fetch and merge results from ALL configured backends.
 * Used for the runs list where we want to see runs from every backend.
 * Returns a JSON Response with the merged array.
 *
 * Modal-style backends are queried via HTTP. Convex-direct backends are
 * queried via the Convex JS client (only the `/runs` listing path is
 * currently supported by Convex direct).
 */
export async function fetchAllBackends(path: string): Promise<Response> {
  const backends = getBackendUrls();
  const headers = new Headers();
  // Token only needed for HTTP backends; Convex queries are public-by-default.
  let httpToken: string | null = null;
  try {
    httpToken = getBackendToken();
  } catch {
    httpToken = null;
  }
  if (httpToken) headers.set("Authorization", `Bearer ${httpToken}`);

  type BackendResult = { backend: string; data: unknown[] };
  const results: BackendResult[] = [];

  await Promise.all(
    backends.map(async (backend) => {
      try {
        let data: unknown;
        if (isConvexUrl(backend)) {
          // Only the /runs listing path is supported via Convex direct
          if (path !== "/runs") return;
          data = await fetchConvexRuns(backend);
        } else {
          if (!httpToken) return;
          const url = buildUrl(backend, path);
          const response = await fetch(url, {
            headers,
            cache: "no-store",
          });
          if (!response.ok) return;
          data = await response.json();
        }

        if (Array.isArray(data)) {
          // Tag each item with its backend so the UI can route detail requests
          const tagged = data.map((item: unknown) =>
            typeof item === "object" && item !== null
              ? { ...item, _backend: backend }
              : item,
          );
          results.push({ backend, data: tagged });
        }
      } catch {
        // ignore failed backends
      }
    }),
  );

  // Merge all results into a single flat array, deduplicating by run_id
  const seen = new Set<string>();
  const merged: unknown[] = [];
  for (const { data } of results) {
    for (const item of data) {
      if (typeof item === "object" && item !== null && "run_id" in item) {
        const id = String((item as { run_id: unknown }).run_id);
        if (!seen.has(id)) {
          seen.add(id);
          merged.push(item);
        }
      }
    }
  }

  return Response.json(merged);
}
