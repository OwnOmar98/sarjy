// Read-only — no checkAuthOrigin, unlike the three mutating auth routes
// (nothing to protect against a cross-origin GET that only reads back
// whatever the caller's own cookie already grants them).

export default defineEventHandler(async (event) => {
  const cookie = getSessionCookieValue(event);
  const session = cookie ? await verifySessionToken(cookie) : null;
  if (!session) return { authenticated: false };

  const sql = getDb();
  const [row] = await sql`select email from users where id = ${session.uid}`;
  if (!row) return { authenticated: false };

  return { authenticated: true, id: session.uid, email: row.email };
});
