// Mints a LiveKit room-join token.
// TODO(day 1): scope identity to a stable per-browser anonymous id
// (docs/PRD.md "Non-goals") so memory can key off it.

import { AccessToken } from "livekit-server-sdk";

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig();
  const identity = getQuery(event).identity?.toString() ?? `user-${Date.now()}`;
  // Unique per session, not a fixed name: automatic agent dispatch fires
  // once per *new* room, not per participant join — reusing one room
  // name across Stop/Start cycles left the second session with no agent.
  const room = `sarjy-${Date.now()}`;

  const at = new AccessToken(config.livekitApiKey, config.livekitApiSecret, {
    identity,
  });
  at.addGrant({ room, roomJoin: true, canPublish: true, canSubscribe: true });

  return { token: await at.toJwt(), url: config.public.livekitUrl };
});
