// Shared Postgres client for the session-history read routes. Read-only
// in practice (agent/db.py owns every write) — this exists purely so the
// browser can list/read what the agent already persisted, without
// routing every page load through the agent's own process.

import postgres from "postgres";

let sql: postgres.Sql | null = null;

export function getDb() {
  if (!sql) {
    sql = postgres(useRuntimeConfig().databaseUrl);
  }
  return sql;
}
