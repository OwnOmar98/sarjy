<!--
  Optional signup/login (docs/PLAN_AUTH.md) — shared by app/pages/login.vue
  and app/pages/signup.vue. Previously login.vue toggled between the two
  modes with local component state, which meant a refresh (or sharing a
  link) always landed back on the login form — a real route per mode
  fixes that; this component just supplies the mode-dependent text/action
  via props rather than duplicating the form twice.
-->
<script setup lang="ts">
const props = defineProps<{ mode: "login" | "signup" }>();

const { t, locale, locales, setLocale } = useI18n();
const { currentUser, login, signup } = useAuth();

const email = ref("");
const password = ref("");
const submitting = ref(false);
const errorKey = ref<string | null>(null);

// Already logged in (a returning visit, or a fresh login just landed
// here from a stale bookmark) — nothing to do here.
watchEffect(() => {
  if (currentUser.value) navigateTo("/");
});

function messageKeyFor(status: number | undefined): string {
  if (status === 409) return "emailAlreadyInUse";
  if (status === 400) return "invalidSignupDetails";
  return "invalidCredentials";
}

async function submit() {
  if (submitting.value) return;
  submitting.value = true;
  errorKey.value = null;
  try {
    if (props.mode === "signup") {
      await signup(email.value, password.value);
    } else {
      await login(email.value, password.value);
    }
    await navigateTo("/");
  } catch (err) {
    const status = (err as { statusCode?: number })?.statusCode;
    errorKey.value = messageKeyFor(status);
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <v-main
    class="lattice-surface login-page"
    style="--lattice-color: rgba(43, 36, 28, 0.035)"
  >
    <!-- This page has no toolbar (SarjyApp.vue's) of its own, so without
    this there's no way back to the app except the browser's own back
    button — which does nothing useful if this route was opened directly
    (a bookmark, a refresh, a shared link). The wordmark alone (no arrow)
    tested as unclear that it's a link back — the arrow is what actually
    signals "this goes back", same hand-drawn line-icon style as the
    toolbar's own menu-toggle/mute icons elsewhere in the app, not an
    icon font. -->
    <NuxtLink to="/" class="login-brand">
      <svg
        class="login-brand__icon"
        width="16"
        height="16"
        viewBox="0 0 20 20"
        fill="none"
        aria-hidden="true"
      >
        <line x1="17" y1="10" x2="3" y2="10" />
        <polyline points="9,4 3,10 9,16" />
      </svg>
      {{ t("title") }}
    </NuxtLink>

    <!-- SarjyApp.vue's toolbar has this too, but this page renders no
    toolbar of its own — without a copy here, landing directly on
    /login or /signup (a bookmark, a shared link, a refresh) leaves no
    way to switch language short of going back to "/" first. -->
    <v-btn-toggle
      :model-value="locale"
      mandatory
      density="compact"
      class="login-lang-toggle"
      @update:model-value="setLocale"
    >
      <v-btn v-for="l in locales" :key="l.code" :value="l.code" size="small">
        {{ l.code.toUpperCase() }}
      </v-btn>
    </v-btn-toggle>

    <v-container
      fluid
      class="d-flex flex-column justify-center login-container"
    >
      <form class="login-form" @submit.prevent="submit">
        <h1 class="login-form__heading">
          {{ mode === "login" ? t("logIn") : t("signUp") }}
        </h1>

        <label class="login-form__label" for="login-email">{{
          t("email")
        }}</label>
        <input
          id="login-email"
          v-model="email"
          class="login-form__input"
          type="email"
          autocomplete="email"
          required
          :disabled="submitting"
        />

        <label class="login-form__label" for="login-password">{{
          t("password")
        }}</label>
        <input
          id="login-password"
          v-model="password"
          class="login-form__input"
          type="password"
          :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
          required
          :disabled="submitting"
        />

        <p v-if="errorKey" class="connect-error" role="alert">
          {{ t(errorKey) }}
        </p>

        <button type="submit" class="login-form__submit" :disabled="submitting">
          {{
            submitting
              ? t("pleaseWait")
              : mode === "login"
                ? t("logIn")
                : t("signUp")
          }}
        </button>

        <NuxtLink
          class="login-form__toggle"
          :to="mode === 'login' ? '/signup' : '/login'"
        >
          {{ mode === "login" ? t("needAccount") : t("haveAccount") }}
        </NuxtLink>
      </form>
    </v-container>
  </v-main>
</template>

<style scoped>
.login-page {
  height: 100dvh;
}

.login-container {
  min-height: 100%;
}

/* Same position/weight as SarjyApp.vue's v-toolbar-title — reads as the
   same brand mark, just without the rest of that toolbar (this page
   doesn't need the auth link) other than the language toggle below. */
.login-brand {
  position: absolute;
  inset-block-start: 1rem;
  inset-inline-start: 1.5rem;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 1.25rem;
  font-weight: 500;
  color: inherit;
  text-decoration: none;
}

.login-brand:hover {
  color: rgb(var(--v-theme-primary));
}

.login-brand__icon {
  flex-shrink: 0;
  /* RTL: logical inset-inline-start above already flips the link's own
     position, but an SVG's internal geometry doesn't mirror on its own —
     without this the arrow would still point left (visually "forward",
     into the page) when the layout direction is Arabic. */
  transform: scaleX(var(--login-brand-icon-flip, 1));
}

[dir="rtl"] .login-brand__icon {
  --login-brand-icon-flip: -1;
}

.login-brand__icon line,
.login-brand__icon polyline {
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* Mirrors .login-brand's position on the opposite side — same corner the
   language toggle sits in on SarjyApp.vue's real toolbar. */
.login-lang-toggle {
  position: absolute;
  inset-block-start: 1rem;
  inset-inline-end: 1.5rem;
}

/* Scoped styles don't leak across components — .main-narrow and
   .connect-error are also only defined in SarjyApp.vue's own
   <style scoped>. */
.login-form {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 24rem;
  margin-inline: auto;
}

.connect-error {
  margin-block-start: 1rem;
  margin-block-end: 1rem;
  padding: 0.5rem 1rem;
  border: 1px solid var(--state-error);
  color: var(--state-error);
  font-size: 0.85rem;
}

.login-form__heading {
  margin-block-end: 1.5rem;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 1.4rem;
  font-weight: 600;
  text-align: center;
}

.login-form__label {
  margin-block-end: 0.35rem;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-muted);
}

.login-form__input {
  width: 100%;
  margin-block-end: 1rem;
  padding: 0.6rem 0.85rem;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 0.95rem;
  color: inherit;
  background-color: rgb(var(--v-theme-surface));
  border: 1.5px solid var(--ink-muted);
  border-radius: 0;
}

.login-form__input:focus-visible {
  outline: none;
  border-color: rgb(var(--v-theme-primary));
}

/* .aperture-button is a scoped class in SarjyApp.vue — Vue's scoped
   styles don't leak across components, so the octagon-cut primary-action
   look has to be redefined here rather than just reusing that class name
   (same duplication this codebase already accepts: no shared component
   library exists, every component hand-rolls its own <style scoped>). */
.login-form__submit {
  clip-path: polygon(
    30% 0%,
    70% 0%,
    100% 30%,
    100% 70%,
    70% 100%,
    30% 100%,
    0% 70%,
    0% 30%
  );
  margin-block-start: 0.5rem;
  padding: 1rem 2rem;
  width: 100%;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 1rem;
  font-weight: 600;
  color: rgb(var(--v-theme-surface));
  background-color: rgb(var(--v-theme-primary));
  border: none;
  cursor: pointer;
  transition: transform 0.15s ease;
}

.login-form__submit:hover:not(:disabled) {
  transform: scale(1.02);
}

.login-form__submit:focus-visible {
  outline: none;
  filter: drop-shadow(2px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(-2px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(0 2px 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(0 -2px 0 rgb(var(--v-theme-on-surface)));
}

.login-form__submit:active:not(:disabled) {
  transform: scale(0.98);
}

.login-form__submit:disabled {
  cursor: default;
  opacity: 0.6;
}

/* display:block + text-align:center, not the <button>'s old default
   center-aligned text — swapping to a real <a> (NuxtLink, so the toggle
   is a shareable/bookmarkable route instead of local component state)
   dropped that browser default, so it has to be set explicitly here. */
.login-form__toggle {
  display: block;
  width: 100%;
  margin-block-start: 1rem;
  font-size: 0.85rem;
  text-align: center;
  color: var(--ink-muted);
  text-decoration: underline;
}

.login-form__toggle:hover {
  color: rgb(var(--v-theme-primary));
}
</style>
