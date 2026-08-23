// Subscribes to server/routes/ws.ts's live-update channel — the server
// side of the Durable Object pub/sub the sidebar refresh runs on (see
// that file and server/api/internal/notify.post.ts for the other two
// thirds of this). One WebSocket per open tab, reconnecting on drop
// (Workers/Durable Object connections aren't guaranteed to live
// forever — an idle timeout or a redeploy both close them normally,
// not as an error) so a tab left open for a while doesn't silently stop
// getting live updates.
//
// Deliberately not a replacement for ConversationSidebar's own
// fetch-on-load path — this only ever triggers the same refresh() that
// already exists (SarjyApp.vue), so if the socket never connects at all
// (offline, a network that blocks WebSockets, running on Vercel where
// this route 200s but nothing ever publishes to it) the sidebar is
// exactly as correct as it was before this existed, just not instant.
const RECONNECT_DELAY_MS = 2000;

export function useLiveUpdates(onUpdate: () => void) {
  const { getOrCreateIdentity } = useSarjyRoom();
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const identity = getOrCreateIdentity();
    socket = new WebSocket(
      `${protocol}//${location.host}/ws?identity=${identity}`,
    );
    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload?.type === "sessions-changed") onUpdate();
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
