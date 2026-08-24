// Mints a LiveKit room-join token.
// Identity is normally the stable per-browser id useSarjyRoom.ts persists
// in localStorage — agent/memory.py uses it as the Postgres user_id (a
// uuid column), so it must be a real UUID even in the fallback case.

import { AccessToken } from "livekit-server-sdk";

export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig();
  // fallbackToRandom guarantees a non-null result — see resolveIdentity's
  // own contract in server/utils/auth.ts.
  const identity = (await resolveIdentity(getSessionCookieValue(event), {
    queryIdentity: getQuery(event).identity?.toString(),
    fallbackToRandom: true,
  }))!;
  // Unique per session, not a fixed name: automatic agent dispatch fires
  // once per *new* room, not per participant join — a reused room name
  // across Stop/Start cycles would leave the second session with no agent.
  const room = `sarjy-${Date.now()}`;
  // Set only when the user picked "continue" on a specific past
  // conversation — agent/main.py reads this off the participant to fetch
  // that conversation's summary. Not validated here (ownership is
  // enforced where it's read: conversations.get_summary scopes by
  // user_id too), this route only carries it through.
  const resumeSessionId = getQuery(event).resumeSessionId?.toString();

  const at = new AccessToken(config.livekitApiKey, config.livekitApiSecret, {
    identity,
    attributes: resumeSessionId
      ? { resume_session_id: resumeSessionId }
      : undefined,
  });
  at.addGrant({ room, roomJoin: true, canPublish: true, canSubscribe: true });

  return { token: await at.toJwt(), url: config.public.livekitUrl };
});
