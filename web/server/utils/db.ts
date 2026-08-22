// Shared Postgres client for the session-history read routes. Read-only
// in practice (agent/db.py owns every write) — this exists purely so the
// browser can list/read what the agent already persisted, without
// routing every page load through the agent's own process.
//
// Two drivers, picked at runtime, not build time: Cloudflare Workers (one of
// this app's two deploy targets, see nuxt.config.ts's cloudflare_module
// preset) has no raw TCP sockets, only fetch — the `postgres` package's
// client can't open a connection there at all. Neon's `neon()` HTTP driver
// is the fix for that specific constraint, but it only speaks Neon's own
// proxy protocol, not plain Postgres wire — confirmed live, it cannot reach
// local Docker Postgres (`fetch failed`), which is what local dev's
// NUXT_DATABASE_URL points at. Node (local dev, and the other deploy
// target, Vercel) has real TCP sockets and `postgres` already worked fine
// there against both local Postgres and real Neon (Neon speaks standard
// Postgres wire too, not only HTTP) before Cloudflare entered the picture —
// so Node keeps the driver that already worked, and only workerd gets the
// one that has to be different. `navigator.userAgent === "Cloudflare-Workers"`
// is the runtime's own documented way to tell the two apart; both plugin
// docs (Cloudflare's and Neon's) point at this exact check for this exact
// situation.

import { neon, type NeonQueryFunction } from "@neondatabase/serverless";
import postgres from "postgres";

// Explicit generics on the Neon side, not `ReturnType<typeof neon>` — that
// widens back out to the full `arrayMode`/`fullResults` union neon()'s
// overloads can return, which is exactly the ambiguity the two `false`
// defaults below resolve at the call site; naming the type here keeps that
// resolved.
type Sql = NeonQueryFunction<false, false> | postgres.Sql;

let sql: Sql | null = null;

export function getDb(): Sql {
  if (!sql) {
    const url = useRuntimeConfig().databaseUrl;
    const isWorkerd = (globalThis as { navigator?: { userAgent?: string } }).navigator
      ?.userAgent === "Cloudflare-Workers";
    sql = isWorkerd ? neon(url) : postgres(url);
  }
  return sql;
}
