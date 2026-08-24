// Only ever clears this browser's own cookie — sessions are stateless
// signed tokens with no server-side revocation, so a token copied out
// before logout stays valid until it expires (docs/PLAN_AUTH.md's
// "Stateless sessions can't be revoked" tradeoff).

export default defineEventHandler((event) => {
  checkAuthOrigin(event);
  clearSessionCookie(event);
  return { ok: true };
});
