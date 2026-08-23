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

// Cloudflare's own hibernatable-WebSocket state, as crossws's
// cloudflare-durable adapter attaches it to each socket (subscribe() ->
// state.t.add(topic) -> ws.serializeAttachment(state) — see
// node_modules/crossws/dist/adapters/cloudflare-durable.mjs's
// getAttachedState/setAttachedState). Read directly here rather than
// through crossws's own API — see publishToTopic below for why.
interface CloudflareWebSocket {
  deserializeAttachment(): { t?: Set<string> } | null;
  send(data: string): void;
}
interface DurableObjectLike {
  ctx: { getWebSockets(): CloudflareWebSocket[] };
}

// Broadcasts to every socket subscribed to `topic`, individually —
// deliberately not cf.durable.publish() (crossws's own method).
// Reproduced live in production: that method finds only the *first*
// peer subscribed to the topic, and if sending to it throws, gives up
// entirely — nothing else subscribed to that topic gets the message
// either, not just the one bad connection. A tab closed without a clean
// handshake, a network drop, a phone locking mid-call: all of these
// leave a stale entry in Workers' own ctx.getWebSockets() list (this
// app's Durable Object is one shared instance for every user, so it
// accumulates across everyone's connections over the app's lifetime),
// and crossws's fan-out has no resilience against hitting one before it
// reaches a live connection later in the list. This iterates the same
// raw list directly and isolates each send in its own try/catch, so one
// dead socket can never block delivery to the rest.
function publishToTopic(
  durable: DurableObjectLike,
  topic: string,
  data: string,
): number {
  let delivered = 0;
  for (const ws of durable.ctx.getWebSockets()) {
    let state: { t?: Set<string> } | null;
    try {
      state = ws.deserializeAttachment();
    } catch {
      continue;
    }
    if (!state?.t?.has(topic)) continue;
    try {
      ws.send(data);
      delivered++;
    } catch (err) {
      console.error(
        "notify: dropping a stale websocket that failed to send",
        err,
      );
    }
  }
  return delivered;
}

export default defineEventHandler(async (event) => {
  const cf = event.context.cloudflare as
    | {
        durable?: DurableObjectLike;
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

  let delivered = 0;
  try {
    delivered = publishToTopic(
      cf.durable,
      body.identity,
      JSON.stringify(body.event),
    );
  } catch (err) {
    // getWebSockets() itself throwing would be a different, worse
    // failure than a single dead socket (which publishToTopic already
    // isolates) — kept as an outer net rather than letting it propagate
    // as a raw 500, same reasoning as every other best-effort boundary
    // in this file.
    console.error("notify: publishToTopic failed", err);
    return { ok: false, delivered: false };
  }
  return { ok: true, delivered: delivered > 0 };
});
