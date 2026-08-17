// Lists past conversations for one browser identity — scoped by identity
// query param the same way token.get.ts is, not by any auth (docs/PRD.md
// non-goals: no auth). A session with no summary (too short/trivial, or
// still in progress) is a real, expected state, not an error.
//
// Sorted by updated_at, not started_at — "continue" reopens the same
// session row (agent/main.py, agent/conversations.py) rather than
// creating a new one, so updated_at is genuinely "last activity," and
// that's what should move a conversation back to the top when you send
// it a new message.
//
// Keyset (cursor) pagination, not offset — offset pagination re-scans and
// re-sorts everything before the requested page on every request, and
// drifts when a new session gets created (or an existing one reopened,
// moving it in the sort) between pages. Keyset seeks directly off the
// last row's own sort key instead.

const PAGE_SIZE = 20;

export default defineEventHandler(async (event) => {
  const identity = getQuery(event).identity?.toString();
  if (!identity) {
    throw createError({
      statusCode: 400,
      statusMessage: "identity is required",
    });
  }

  const cursorParam = getQuery(event).cursor?.toString();
  const cursor = cursorParam ? decodeCursor(cursorParam) : null;

  const sql = getDb();
  const rows = cursor
    ? await sql`
        select id, started_at, updated_at, ended_at, summary
        from sessions
        where user_id = ${identity}
          and (updated_at, id) < (${cursor[0]}::timestamptz, ${cursor[1]}::uuid)
        order by updated_at desc, id desc
        limit ${PAGE_SIZE + 1}
      `
    : await sql`
        select id, started_at, updated_at, ended_at, summary
        from sessions
        where user_id = ${identity}
        order by updated_at desc, id desc
        limit ${PAGE_SIZE + 1}
      `;

  // Asking for one extra row is the cheap way to know whether there's a
  // next page without a separate count query — if it came back, there's
  // more, and it's dropped rather than returned.
  const hasMore = rows.length > PAGE_SIZE;
  const items = hasMore ? rows.slice(0, PAGE_SIZE) : rows;
  const last = items.at(-1);
  const nextCursor =
    hasMore && last
      ? encodeCursor(new Date(last.updated_at).toISOString(), last.id)
      : null;

  return { items, nextCursor };
});
