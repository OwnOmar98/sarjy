// Opaque cursor for keyset pagination — a (sort-column, id) tuple,
// base64-encoded so callers pass one query param instead of two and
// never see or construct the tuple themselves. Used by both
// sessions.get.ts (started_at, id) and sessions/[id]/messages.get.ts
// (created_at, id) — a compound key rather than the timestamp alone,
// since two rows can share the same timestamp and a single-column
// cursor would then silently skip or repeat rows across pages.

// btoa/atob, not Buffer — this project has no @types/node (nothing else
// here needed it), and these are the portable Web-standard equivalent,
// available in Node 16+ globally and on any other Nitro deploy target.
// Both only handle Latin1 input/output; timestamps and UUIDs never leave
// that range, so no UTF-8 encode/decode step is needed either.
function toBase64Url(s: string): string {
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(s: string): string {
  const padded = s.replace(/-/g, "+").replace(/_/g, "/");
  return atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
}

export function encodeCursor(sortValue: string, id: string): string {
  return toBase64Url(JSON.stringify([sortValue, id]));
}

export function decodeCursor(cursor: string): [string, string] | null {
  try {
    const parsed = JSON.parse(fromBase64Url(cursor));
    if (
      Array.isArray(parsed) &&
      parsed.length === 2 &&
      typeof parsed[0] === "string" &&
      typeof parsed[1] === "string"
    ) {
      return parsed as [string, string];
    }
  } catch {
    // Malformed/tampered cursor — treated as "no cursor" by the caller,
    // same as a first page request, rather than a 400.
  }
  return null;
}
