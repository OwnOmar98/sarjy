// Called by the agent (agent/web_notify.py) right after it writes
// something the sidebar or an open transcript cares about — a session
// starting or finishing, or a single message being added — so every
// open tab for that user can update instantly instead of only on its
// own next page load.
//
// Reachable from the public internet (the agent runs on Fly.io, outside
// Cloudflare), so it's the one route in this app that needs its own
// auth: a shared secret, not the "trust the browser's own identity"
// model every other route uses, since this one isn't the browser
// calling it.
//
// This route never inspects `event`'s shape — it's a pure relay. The
// payload contract lives in exactly two other places that actually have
// to agree on it: agent/web_notify.py (what gets sent) and
// app/composables/useLiveUpdates.ts (what gets consumed), both against
// shared/types/conversation.ts's LiveUpdateEvent.
//
// Two-hop on Cloudflare: the Worker that actually receives this POST
// isn't necessarily the same one holding the WebSocket connections —
// those live in the Durable Object (server/routes/ws.ts). A request
// arrives in the plain Worker first (event.context.cloudflare.durable is
// unset there), gets forwarded into the DO via durableFetch(), and only
// the DO-side invocation of this same handler (event.context.cloudflare.
// durable set) actually calls .publish(). Off Cloudflare — local dev,
// or if this ever runs on Vercel — event.context.cloudflare is
// undefined entirely, so this just no-ops: nothing to publish to, and
// the sidebar's existing fetch-on-load path is correct without it.
export default defineEventHandler(async (event) => {
  const cf = event.context.cloudflare as
    | {
        durable?: { publish: (topic: string, data: string) => void };
        durableFetch?: (req: Request) => Promise<Response>;
      }
    | undefined;
  if (!cf) {
    return { ok: true, delivered: false };
  }

  const secret = getHeader(event, "x-internal-secret");
  const body = await readBody<{ identity?: string; event?: LiveUpdateEvent }>(
    event,
  );

  if (!cf.durable) {
    // Forward into the Durable Object with a freshly-built Request, not
    // the original one — by the time a route handler runs, Nitro's own
    // routing has already consumed the original request's body, and a
    // Request can't be replayed once its body stream is drained
    // (confirmed live: "Cannot reconstruct a Request with a used body").
    return cf.durableFetch!(
      new Request(getRequestURL(event), {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(secret ? { "x-internal-secret": secret } : {}),
        },
        body: JSON.stringify(body ?? {}),
      }),
    );
  }

  const expected = useRuntimeConfig().internalNotifySecret;
  if (!expected || secret !== expected) {
    throw createError({ statusCode: 401, statusMessage: "unauthorized" });
  }
  if (!body?.identity || !body.event) {
    throw createError({
      statusCode: 400,
      statusMessage: "identity and event are required",
    });
  }

  // TEMPORARY diagnostic try/catch — reverting once the production 500
  // this is chasing is actually identified.
  try {
    cf.durable.publish(body.identity, JSON.stringify(body.event));
  } catch (err) {
    return {
      ok: false,
      diagnosticError: err instanceof Error ? err.message : String(err),
      diagnosticStack: err instanceof Error ? err.stack : undefined,
    };
  }
  return { ok: true, delivered: true };
});
