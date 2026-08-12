// Mints a LiveKit room-join token.
// Identity is normally the stable per-browser id useSarjyRoom.ts persists
// in localStorage — agent/memory.py uses it as the Postgres user_id (a
// uuid column), so it must be a real UUID even in the fallback case.

import { AccessToken } from "livekit-server-sdk";

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig();
  const identity = getQuery(event).identity?.toString() ?? crypto.randomUUID();
  // Unique per session, not a fixed name: automatic agent dispatch fires
  // once per *new* room, not per participant join — a reused room name
  // across Stop/Start cycles would leave the second session with no agent.
  const room = `sarjy-${Date.now()}`;

  const at = new AccessToken(config.livekitApiKey, config.livekitApiSecret, {
    identity,
  });
  at.addGrant({ room, roomJoin: true, canPublish: true, canSubscribe: true });

  return { token: await at.toJwt(), url: config.public.livekitUrl };
});
