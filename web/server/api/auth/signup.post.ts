// Optional signup (docs/PLAN_AUTH.md). Claims the caller's own anonymous
// identity in place (ON CONFLICT below) rather than always minting a
// brand-new row — a browser's existing sessions/facts/calendar_events
// (all FK'd to users.id) stay attached automatically, with no separate
// migration step. useAuth.ts's signup() sends getOrCreateIdentity()'s
// current value as `identity` for exactly this, then immediately calls
// resetIdentity() so this same browser gets a fresh, unclaimed anonymous
// id going forward — without that, a later logout would leave this
// browser permanently sending a now-password-protected uid as its guest
// identity, and every future anonymous call would 401 forever
// (resolveIdentity in server/utils/auth.ts).

const MIN_PASSWORD_LENGTH = 8;

export default defineEventHandler(async (event) => {
  checkAuthOrigin(event);

  const body = await readBody<{
    email?: string;
    password?: string;
    identity?: string;
  }>(event);
  const email = body?.email?.trim().toLowerCase();
  const password = body?.password;
  const identity = body?.identity;

  if (!email || !email.includes("@")) {
    throw createError({
      statusCode: 400,
      statusMessage: "a valid email is required",
    });
  }
  if (!password || password.length < MIN_PASSWORD_LENGTH) {
    throw createError({
      statusCode: 400,
      statusMessage: `password must be at least ${MIN_PASSWORD_LENGTH} characters`,
    });
  }

  const passwordHash = await hashPassword(password);
  const sql = getDb();

  let userId: string;
  try {
    if (identity && UUID_RE.test(identity)) {
      // ON CONFLICT DO UPDATE, guarded by password_hash is null — claims
      // the row only if it's still genuinely anonymous. Without that
      // guard this would let anyone who merely knows (or guesses) another
      // user's anonymous UUID overwrite that account's password — a real
      // account-takeover path, not a hypothetical one, since signup runs
      // before any session cookie exists and so can't otherwise tell
      // "my own anonymous browser" from "someone else's". If the row
      // already belongs to a real account, the WHERE clause blocks the
      // update and RETURNING comes back empty.
      const [row] = await sql`
        insert into users (id, email, password_hash)
        values (${identity}, ${email}, ${passwordHash})
        on conflict (id) do update
          set email = excluded.email, password_hash = excluded.password_hash
          where users.password_hash is null
        returning id
      `;
      if (!row) {
        throw createError({
          statusCode: 409,
          statusMessage: "that identity is already registered",
        });
      }
      userId = row.id;
    } else {
      const [row] = await sql`
        insert into users (email, password_hash)
        values (${email}, ${passwordHash})
        returning id
      `;
      userId = row!.id;
    }
  } catch (err) {
    // Postgres unique_violation on users_email_lower_idx — two signups
    // racing on the same email (possibly differently-cased), not a rare
    // hypothetical (see the plan's "Signup races" section).
    if ((err as { code?: string }).code === "23505") {
      throw createError({
        statusCode: 409,
        statusMessage: "email already in use",
      });
    }
    throw err;
  }

  await issueSession(event, userId);
  return { id: userId, email };
});
