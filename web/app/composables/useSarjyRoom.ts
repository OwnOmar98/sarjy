// Connects to the LiveKit room the agent joins.
// TODO(day 1): mute controls, reconnect handling.
// TODO(day 2): subscribe to the agent's "latency" topic (agent/latency.py)
// and feed LatencyHud.vue.

import { Room, RoomEvent } from "livekit-client";

export function useSarjyRoom() {
  const room = new Room();
  const connected = ref(false);

  async function connect() {
    const { token, url } = await $fetch<{ token: string; url: string }>(
      "/api/token",
    );
    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
    connected.value = true;
  }

  function disconnect() {
    room.disconnect();
  }

  room.on(RoomEvent.Disconnected, () => {
    connected.value = false;
  });

  return { room, connected, connect, disconnect };
}
