// Subscribes to server/routes/ws.ts's live-update channel — the server
// side of the Durable Object pub/sub (see that file and server/api/
// internal/notify.post.ts for the other two thirds of this, and
// shared/types/conversation.ts for the LiveUpdateEvent payload shape
// every part of this agrees on). One WebSocket per open tab, reconnecting
// on drop (Workers/Durable Object connections aren't guaranteed to live
// forever — an idle timeout or a redeploy both close them normally, not
// as an error) so a tab left open for a while doesn't silently stop
// getting live updates.
//
// Deliberately not a replacement for ConversationSidebar's/
// SelectedConversationTranscript's own fetch-on-load paths — if the
// socket never connects at all (offline, a network that blocks
// WebSockets, running on Vercel where this route 200s but nothing ever
// publishes to it), both are exactly as correct as they were before this
// existed, just not instant.
const RECONNECT_DELAY_MS = 2000;

export function useLiveUpdates(handlers: {
  onEvent: (event: LiveUpdateEvent) => void;
  // Fires when the socket re-opens after having been open before — never
  // on the very first connect. This is the one moment "just apply
  // whatever arrives" (onEvent) isn't enough on its own: events missed
  // while disconnected (a network blip, the tab backgrounded, a Worker
  // eviction) are gone for good unless something reconciles against the
  // database afterward, so this is where a full catch-up refresh belongs
  // — not on every event, which would defeat the point of pushing data
  // directly instead of just a signal.
  onReconnect: () => void;
}) {
  const { getOrCreateIdentity } = useSarjyRoom();
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let hasConnectedBefore = false;

  function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const identity = getOrCreateIdentity();
    socket = new WebSocket(
      `${protocol}//${location.host}/ws?identity=${identity}`,
    );
    socket.addEventListener("open", () => {
      if (hasConnectedBefore) {
        handlers.onReconnect();
      } else {
        hasConnectedBefore = true;
      }
    });
    socket.addEventListener("message", (event) => {
      try {
        handlers.onEvent(JSON.parse(event.data));
      } catch {
        // Not a message shape this channel is expected to send — ignore
        // rather than throw, the same way a malformed transcript segment
        // elsewhere in this app is dropped rather than crashing the tab.
      }
    });
    socket.addEventListener("close", scheduleReconnect);
    socket.addEventListener("error", () => socket?.close());
  }

  function scheduleReconnect() {
    if (stopped || reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, RECONNECT_DELAY_MS);
  }

  onMounted(connect);
  onUnmounted(() => {
    stopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    socket?.close();
  });
}
