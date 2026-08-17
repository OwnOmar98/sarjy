// Turn-by-turn transcript for one past conversation, keyset-paginated the
// same way sessions.get.ts is (see that file for why keyset over offset).
// Requires the caller's identity to match sessions.user_id — the id
// alone is a UUID a browser could otherwise pass unchanged to read
// someone else's conversation; same scoping rule as
// agent/conversations.py's resume_context().
//
// Newest-first internally (mirrors a chat app opening scrolled to the
// bottom): the first page is the most recent PAGE_SIZE messages, and
// paging forward loads what came *before* them — older history, not
// "what's next" the way sessions.get.ts pages forward through newer
// conversations. Every page is reversed to ascending order before it's
// returned, so the response contract is always "oldest first" regardless
// of which direction was actually queried; the caller prepends each page
// it receives rather than appending.

const PAGE_SIZE = 30;

// A syntactically invalid id (a stale/hand-edited URL, a typo) is just as
// much "not found" as a well-formed one with no matching row — but
// handing it straight to postgres as a ::uuid comparison throws a raw
// "invalid input syntax for type uuid" error instead, a 500 the frontend's
// 404-only catch doesn't recognize as "show the not-found state" (confirmed
// live: aperture button stayed up, no error surfaced). Reject it here,
// before it ever reaches sql, as the same 404 a missing row gets.
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default defineEventHandler(async (event) => {
  const sessionId = getRouterParam(event, "id");
  const identity = getQuery(event).identity?.toString();
  if (!identity) {
    throw createError({
      statusCode: 400,
      statusMessage: "identity is required",
    });
  }
  if (!sessionId || !UUID_RE.test(sessionId)) {
    throw createError({ statusCode: 404, statusMessage: "not found" });
  }

  const sql = getDb();
  const [session] = await sql`
    select id from sessions where id = ${sessionId} and user_id = ${identity}
  `;
  if (!session) {
    throw createError({ statusCode: 404, statusMessage: "not found" });
  }

  const cursorParam = getQuery(event).cursor?.toString();
  const cursor = cursorParam ? decodeCursor(cursorParam) : null;

  const rows = cursor
    ? await sql`
        select id, role, content, created_at
        from messages
        where session_id = ${sessionId!}
          and (created_at, id) < (${cursor[0]}::timestamptz, ${cursor[1]}::uuid)
        order by created_at desc, id desc
        limit ${PAGE_SIZE + 1}
      `
    : await sql`
        select id, role, content, created_at
        from messages
        where session_id = ${sessionId!}
        order by created_at desc, id desc
        limit ${PAGE_SIZE + 1}
      `;

  const hasMore = rows.length > PAGE_SIZE;
  const items = (hasMore ? rows.slice(0, PAGE_SIZE) : rows).reverse();
  // The oldest message in *this* batch — after reversing, that's the
  // first element — is the seek point for "give me the page before this
  // one."
  const oldest = items[0];
  const nextCursor =
    hasMore && oldest
      ? encodeCursor(new Date(oldest.created_at).toISOString(), oldest.id)
      : null;

  return { items, nextCursor };
});
