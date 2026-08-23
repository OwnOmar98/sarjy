// Live push channel for the conversation sidebar
// (app/components/ConversationSidebar.vue) — every open browser tab
// connects here and subscribes to a topic named after its own identity
// (the same uuid useSarjyRoom.ts already sends as ?identity= on every
// other API call), so a publish reaches every tab for that user and no
// one else's.
//
// Under Cloudflare (wrangler.jsonc's durable_objects binding), Nitro's
// cloudflare-durable preset transparently routes every WebSocket
// connection through the one shared Durable Object regardless of which
// Worker instance/region handled the original upgrade request — that's
// what makes server/api/internal/notify.post.ts's publish() reach a tab
// connected via a different request entirely. Under plain Node (local
// dev), the same handler still runs — crossws keeps peers/topics
// in-process there — but nothing calls .publish() locally yet (see
// notify.post.ts), so a live push during local dev is a known,
// acceptable gap: the sidebar's existing fetch-on-load path already
// covers that case today, unaffected either way.
export default defineWebSocketHandler({
  open(peer) {
    const identity = new URL(
      peer.request?.url ?? "",
      "http://internal",
    ).searchParams.get("identity");
    if (identity) peer.subscribe(identity);
  },
});
