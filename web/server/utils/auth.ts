// Optional signup/login (docs/PLAN_AUTH.md) — every function here uses
// only the Web Crypto API (crypto.subtle, crypto.getRandomValues, global
// btoa/atob), never Node's `crypto` module or any npm crypto package.
// This project was already burned once this session by an npm package
// (the `postgres` TCP driver, server/utils/db.ts) that worked under Node
// but silently couldn't run under Cloudflare Workers, one of this app's
// two deploy targets — Web Crypto is a global under both runtimes, so
// there's nothing here to repeat that mistake with.

// h3's runtime helpers (getCookie, setCookie, createError, ...) are
// auto-imported by Nitro everywhere they're already used in this repo
// (e.g. server/api/internal/notify.post.ts); the H3Event *type* isn't
// part of that auto-import, unlike this project's own shared/ types
// (web/shared/types/conversation.ts) — h3 is a third-party package, so
// its types still need an explicit import.
import type { H3Event } from "h3";

// 100,000, not the originally-planned 210,000 (OWASP 2023) — confirmed
// live against the real deployed Worker (not assumed, per this file's
// own top-of-file lesson): Cloudflare's crypto.subtle.deriveBits hard-
// rejects PBKDF2 above 100,000 iterations ("Pbkdf2 failed: iteration
// counts above 100000 are not supported"), independent of any CPU-time
// budget — every signup/login 500'd in production until this was capped
// to the platform's actual ceiling. 100,000 is still a solid, widely-used
// PBKDF2-HMAC-SHA256 iteration count, just not the newest highest
// recommendation.
const PBKDF2_ITERATIONS = 100_000;
const SALT_BYTES = 16;

// The one thing kept as a literal rather than computed: a fixed dummy
// PBKDF2 hash (real salt, real iterations, password is a throwaway
// string) that verifyPassword() below runs against whenever no matching
// user row exists, in login.post.ts. Without this, "no such email"
// returns instantly while "wrong password" pays for a real PBKDF2
// derivation (~100-300ms) — a textbook timing oracle that reveals which
// emails are registered. Generated once, hardcoded, not derived at
// module load: a real derivation at import time would mean every cold
// start pays this cost regardless of whether login is ever called.
const DUMMY_PASSWORD_HASH =
  "pbkdf2$100000$x0Mezk_RgMrxHI25YO7Rlw$VNctsIRg9Nmk_FILgUyN1yO8rrCizY2JeqtVsLsxagY";

// Exported so server/routes/ws.ts (no H3Event, reads the cookie header
// directly off peer.request) can name the same cookie without duplicating
// the string.
export const SESSION_COOKIE_NAME = "sarjy_session";
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60; // 30 days — no server-side
// revocation exists (stateless HMAC tokens, see verifySessionToken), so
// "logout" only ever clears this browser's own cookie; a token copied out
// beforehand stays valid until it expires regardless. Keeping the TTL
// short-ish is the only mitigation available without adding real
// server-side session state (a token_version column, checked at verify
// time) — a reasonable follow-up if "logout everywhere" is ever wanted,
// not built now.

// Same pattern already used for session ids in
// server/api/sessions/[id]/messages.get.ts. Exported so
// server/api/auth/signup.post.ts can validate a claimed identity with the
// same rule this file's own resolveIdentity uses, rather than a second
// hand-copied regex.
export const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

// Uint8Array<ArrayBuffer>, not the bare Uint8Array (= Uint8Array<ArrayBufferLike>)
// default — lib.dom's BufferSource excludes SharedArrayBuffer-backed views, and
// the wider ArrayBufferLike return type doesn't satisfy that at the
// crypto.subtle.* call sites below even though every array here is a plain
// ArrayBuffer at runtime.
function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  const padded = value
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// Byte-for-byte constant-time compare — never short-circuits, even on a
// length mismatch (XOR the lengths into the same accumulator rather than
// returning early), so neither the content nor the length of a wrong
// guess leaks via timing. Only needed for the PBKDF2 hash comparison
// below; crypto.subtle.verify() (used for the session-token HMAC further
// down) is already constant-time in every engine backing both this app's
// runtimes (V8/BoringSSL, workerd/BoringSSL) — do not add a redundant
// hand-rolled compare there too.
function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  const len = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < len; i++) {
    diff |= (a[i] ?? 0) ^ (b[i] ?? 0);
  }
  return diff === 0;
}

async function derivePbkdf2(
  password: string,
  salt: Uint8Array<ArrayBuffer>,
  iterations: number,
): Promise<Uint8Array<ArrayBuffer>> {
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    keyMaterial,
    256,
  );
  return new Uint8Array(bits);
}

export async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const hash = await derivePbkdf2(password, salt, PBKDF2_ITERATIONS);
  return `pbkdf2$${PBKDF2_ITERATIONS}$${toBase64Url(salt)}$${toBase64Url(hash)}`;
}

export async function verifyPassword(
  password: string,
  stored: string,
): Promise<boolean> {
  const parts = stored.split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2") return false;
  const iterations = Number.parseInt(parts[1]!, 10);
  const salt = fromBase64Url(parts[2]!);
  const expected = fromBase64Url(parts[3]!);
  const actual = await derivePbkdf2(password, salt, iterations);
  return timingSafeEqual(actual, expected);
}

// login.post.ts calls this (never verifyPassword directly against a
// literal "no such user" branch) so a missing-email lookup pays the same
// PBKDF2 cost as a real wrong-password check — see DUMMY_PASSWORD_HASH.
export async function verifyPasswordTimingSafe(
  password: string,
  stored: string | null,
): Promise<boolean> {
  const result = await verifyPassword(password, stored ?? DUMMY_PASSWORD_HASH);
  return stored !== null && result;
}

async function getHmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signSessionToken(
  uid: string,
  ttlSeconds: number,
): Promise<string> {
  const secret = useRuntimeConfig().sessionSecret;
  const payload = JSON.stringify({
    uid,
    exp: Math.floor(Date.now() / 1000) + ttlSeconds,
  });
  const payloadB64 = toBase64Url(new TextEncoder().encode(payload));
  const key = await getHmacKey(secret);
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(payloadB64),
  );
  return `${payloadB64}.${toBase64Url(new Uint8Array(signature))}`;
}

export async function verifySessionToken(
  token: string,
): Promise<{ uid: string } | null> {
  const [payloadB64, sigB64] = token.split(".");
  if (!payloadB64 || !sigB64) return null;
  const secret = useRuntimeConfig().sessionSecret;
  const key = await getHmacKey(secret);
  const valid = await crypto.subtle.verify(
    "HMAC",
    key,
    fromBase64Url(sigB64),
    new TextEncoder().encode(payloadB64),
  );
  if (!valid) return null;
  let payload: { uid?: string; exp?: number };
  try {
    payload = JSON.parse(new TextDecoder().decode(fromBase64Url(payloadB64)));
  } catch {
    return null;
  }
  if (
    !payload.uid ||
    !payload.exp ||
    payload.exp < Math.floor(Date.now() / 1000)
  ) {
    return null;
  }
  return { uid: payload.uid };
}

export async function issueSession(event: H3Event, uid: string): Promise<void> {
  const token = await signSessionToken(uid, SESSION_TTL_SECONDS);
  setCookie(event, SESSION_COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
    // Computed from the actual incoming request, not hardcoded — matches
    // the existing precedent in server/api/internal/notify.post.ts.
    // Hardcoding true would silently break every local-dev cookie (plain
    // http://localhost never gets a browser to send a Secure cookie back).
    secure: getRequestURL(event).protocol === "https:",
  });
}

export function clearSessionCookie(event: H3Event): void {
  deleteCookie(event, SESSION_COOKIE_NAME, { path: "/" });
}

export function getSessionCookieValue(event: H3Event): string | undefined {
  return getCookie(event, SESSION_COOKIE_NAME);
}

// server/routes/ws.ts's WebSocket open(peer) hook has no H3Event — only
// peer.request.headers, a real Headers object confirmed (by reading
// crossws's own adapter source, not assumed) to carry the Cookie header
// identically on both this app's deploy targets at open() time.
export function parseCookieFromHeader(
  cookieHeader: string | null,
  name: string,
): string | undefined {
  if (!cookieHeader) return undefined;
  for (const part of cookieHeader.split(";")) {
    const eq = part.indexOf("=");
    if (eq === -1) continue;
    if (part.slice(0, eq).trim() !== name) continue;
    try {
      return decodeURIComponent(part.slice(eq + 1).trim());
    } catch {
      return part.slice(eq + 1).trim();
    }
  }
  return undefined;
}

// The login-CSRF fix: SameSite=Lax stops the session cookie from being
// *sent* cross-site, but does nothing to stop a third-party page from
// POSTing to /api/auth/login with the *attacker's own* credentials and
// getting back a Set-Cookie the victim's browser will still honor —
// Set-Cookie on a response isn't gated by the request's SameSite status.
// Without this check, visiting a malicious page would silently log a
// victim into the attacker's account, and anything they said to the
// agent afterward would be written into data the attacker can read.
// Origin is what a real fetch() POST always sends; Referer is the
// fallback for the rare client that omits it. Absence of both is allowed
// through — there's nothing to compare against, and rejecting outright
// risks breaking a legitimate client that sends neither.
export function checkAuthOrigin(event: H3Event): void {
  const appOrigin = getRequestURL(event).origin;
  const origin = getHeader(event, "origin");
  if (origin) {
    if (origin !== appOrigin) {
      throw createError({
        statusCode: 403,
        statusMessage: "cross-origin request rejected",
      });
    }
    return;
  }
  const referer = getHeader(event, "referer");
  if (referer && !referer.startsWith(appOrigin)) {
    throw createError({
      statusCode: 403,
      statusMessage: "cross-origin request rejected",
    });
  }
}

// The core identity-resolution model every route that used to just trust
// a client-supplied `identity` query param now goes through:
//
// 1. A valid session cookie always wins, full stop — ignore whatever the
//    client also claims in queryIdentity once one exists.
// 2. No cookie: fall back to today's existing behavior (trust the
//    client-supplied identity) UNLESS that identity belongs to a real,
//    password-protected account — a bare UUID isn't a secret, so without
//    this check "login" would protect nothing; anyone could still just
//    pass a real account's id and read their data from a browser that
//    was never logged into it.
// 3. That DB check fails open, not closed, on error. ensure_user() runs
//    from five independent places agent-side (confirmed live via grep:
//    db.py, conversations.py, tools.py x2, memory.py) — meaning almost
//    every anonymous identity that's ever completed a turn already has a
//    users row, so this isn't a rare-fallback query, it's the common
//    case. server/api/token.get.ts has zero Postgres dependency today;
//    a transient DB error on this new check should degrade anonymous
//    calling exactly as it already does today (i.e. not at all), not
//    take down the ability to start a call.
//
// queryIdentity that isn't a well-formed UUID skips the DB check
// entirely and passes through unchanged — the caller's own downstream
// query already fails on malformed input today (Postgres's ::uuid cast),
// same as before this feature existed; this function's job is only to
// decide whether an *account* needs protecting, not to add new
// validation behavior beyond that.
export async function resolveIdentity(
  sessionCookie: string | undefined,
  opts: { queryIdentity?: string; fallbackToRandom?: boolean },
): Promise<string | null> {
  if (sessionCookie) {
    const session = await verifySessionToken(sessionCookie);
    if (session) return session.uid;
  }

  const { queryIdentity, fallbackToRandom } = opts;
  if (queryIdentity) {
    if (UUID_RE.test(queryIdentity)) {
      let isPasswordProtected = false;
      try {
        const sql = getDb();
        const rows =
          await sql`select password_hash from users where id = ${queryIdentity}`;
        isPasswordProtected = rows.length > 0 && rows[0]!.password_hash != null;
      } catch {
        // Fail open — see the function-level comment above.
        isPasswordProtected = false;
      }
      if (isPasswordProtected) {
        throw createError({ statusCode: 401, statusMessage: "unauthorized" });
      }
    }
    return queryIdentity;
  }

  if (fallbackToRandom) return crypto.randomUUID();
  return null;
}
