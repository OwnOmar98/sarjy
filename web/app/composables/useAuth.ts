// Optional signup/login (docs/PLAN_AUTH.md) — auth is opt-in, anonymous
// (useSarjyRoom.ts's localStorage identity) stays the zero-friction
// default. currentUser/authLoading are module-level, not created fresh
// per useAuth() call — every component that renders auth state (the
// toolbar, login.vue) needs to see the SAME user, not its own isolated
// copy the way useSarjyRoom's per-instance Room is deliberately scoped.

export interface AuthUser {
  id: string;
  email: string;
}

const currentUser = ref<AuthUser | null>(null);
// True until the initial /api/auth/me check resolves — lets the toolbar
// render nothing rather than flashing "Log in" for a moment before a
// real session cookie is confirmed.
const authLoading = ref(true);
let initialized = false;

async function fetchMe() {
  try {
    const res = await $fetch<{
      authenticated: boolean;
      id?: string;
      email?: string;
    }>("/api/auth/me");
    currentUser.value =
      res.authenticated && res.id && res.email
        ? { id: res.id, email: res.email }
        : null;
  } catch {
    // Not fatal — same as every other best-effort auth-state read here;
    // treat a failed check as "not logged in" rather than surfacing an
    // error for a call nothing in the UI triggered directly.
    currentUser.value = null;
  } finally {
    authLoading.value = false;
  }
}

export function useAuth() {
  // Runs once for the whole app's lifetime, on whichever component
  // happens to call useAuth() first — not onMounted, since the toolbar
  // (always present) and login.vue (only present pre-login) can't both
  // assume they're first.
  if (!initialized) {
    initialized = true;
    void fetchMe();
  }

  async function signup(email: string, password: string) {
    // identity: claims this browser's existing anonymous history in
    // place (server/api/auth/signup.post.ts) instead of orphaning it
    // under an unrelated fresh id. resetIdentity() right after: without
    // it, this browser would keep sending that now-claimed,
    // password-protected uid as its guest identity after a future
    // logout, and every anonymous call would 401 forever — see that
    // function's own comment in useSarjyRoom.ts.
    const user = await $fetch<AuthUser>("/api/auth/signup", {
      method: "POST",
      body: { email, password, identity: getOrCreateIdentity() },
    });
    resetIdentity();
    currentUser.value = user;
  }

  async function login(email: string, password: string) {
    const user = await $fetch<AuthUser>("/api/auth/login", {
      method: "POST",
      body: { email, password },
    });
    currentUser.value = user;
  }

  async function logout() {
    await $fetch("/api/auth/logout", { method: "POST" });
    currentUser.value = null;
  }

  return { currentUser, authLoading, signup, login, logout };
}
