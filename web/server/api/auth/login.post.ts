// Optional login (docs/PLAN_AUTH.md). Missing-email and wrong-password
// both return the exact same 401 message — no user enumeration — and
// both pay the same PBKDF2 cost via verifyPasswordTimingSafe's dummy-hash
// path, so response timing doesn't leak which emails are registered.

export default defineEventHandler(async (event) => {
  checkAuthOrigin(event);

  const body = await readBody<{ email?: string; password?: string }>(event);
  const email = body?.email?.trim().toLowerCase();
  const password = body?.password;

  if (!email || !password) {
    throw createError({
      statusCode: 400,
      statusMessage: "email and password are required",
    });
  }

  const sql = getDb();
  const [row] = await sql`
    select id, password_hash from users where lower(email) = ${email}
  `;

  const ok = await verifyPasswordTimingSafe(
    password,
    row?.password_hash ?? null,
  );
  if (!ok || !row) {
    throw createError({
      statusCode: 401,
      statusMessage: "invalid email or password",
    });
  }

  await issueSession(event, row.id);
  return { id: row.id, email };
});
