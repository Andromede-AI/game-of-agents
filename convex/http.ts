import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { api } from "./_generated/api";

const http = httpRouter();
const syncToken = process.env.CONVEX_SYNC_TOKEN;

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function authorize(request: Request) {
  if (!syncToken) {
    return true;
  }
  return request.headers.get("Authorization") === `Bearer ${syncToken}`;
}

http.route({
  path: "/sync/run",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    if (!authorize(request)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }
    const body = (await request.json()) as { run?: unknown };
    await ctx.runMutation(api.runs.syncRunState, {
      run: body.run ?? body,
      syncToken,
    });
    return json({ ok: true });
  }),
});

http.route({
  path: "/sync/event",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    if (!authorize(request)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }
    const body = (await request.json()) as { event?: unknown };
    await ctx.runMutation(api.runs.appendEvent, {
      event: body.event ?? body,
      syncToken,
    });
    return json({ ok: true });
  }),
});

http.route({
  path: "/sync/batch",
  method: "POST",
  handler: httpAction(async (ctx, request) => {
    if (!authorize(request)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }
    const body = (await request.json()) as {
      run?: unknown;
      events?: unknown[];
    };
    await ctx.runMutation(api.runs.batchSync, {
      run: body.run,
      events: body.events ?? [],
      syncToken,
    });
    return json({ ok: true });
  }),
});

export default http;
