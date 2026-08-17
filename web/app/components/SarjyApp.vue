<!--
  The actual app — everything that was app.vue before conversation
  history needed a real route per conversation. app.vue is now the thin
  Vuetify/NuxtPage shell; this is what app/pages/index.vue and
  app/pages/c/[id].vue both render, the id (if any) coming down as a
  prop from the route rather than living as local state here.
-->
<script setup lang="ts">
const props = defineProps<{ initialSessionId?: string }>();

const {
  connected,
  connecting,
  connectError,
  connect,
  disconnect,
  muted,
  toggleMute,
  micLevel,
  sessionId,
  transcript,
  latencyStages,
  latencyPercentiles,
  awaitingReply,
  ready,
  slowToStart,
  conversationState,
  audioBlocked,
  resumeAudio,
} = useSarjyRoom();

// Derived from the route, not a local ref — app/pages/c/[id].vue is the
// single source of truth for "which conversation is selected" so a
// refresh lands back on the same one. Selecting in the sidebar
// navigates; it never mutates this directly.
const selectedSessionId = computed(() => props.initialSessionId ?? null);
const conversationNotFound = ref(false);
const sidebarOpen = ref(false);
const sidebarRef = ref<{ refresh: () => Promise<void> } | null>(null);
const transcriptRef = ref<{ refresh: () => void } | null>(null);

function selectConversation(id: string) {
  conversationNotFound.value = false;
  navigateTo(`/c/${id}`);
}

function selectNewConversation() {
  conversationNotFound.value = false;
  navigateTo("/");
}

function startOrResume() {
  connect(selectedSessionId.value ?? undefined);
}

// A finished call is exactly when a new (or updated) row shows up in the
// history list — refresh it either way. Where the idle screen lands after
// used to depend on whether this was a resumed conversation (the only
// case with any id to navigate to): a brand-new one always went back to
// "/", with no way to reach the conversation it had just created except
// digging it out of the sidebar — the frontend never sent an id to
// resume, and (before agent/main.py started publishing one over the
// room's "session" data topic) had no way to learn the one the agent
// opened either, confirmed live. sessionId (useSarjyRoom.ts) now covers
// both cases: for a resumed call it matches selectedSessionId already;
// for a fresh one it's the only source of truth for which id to land on.
watch(connected, async (isConnected, wasConnected) => {
  if (wasConnected && !isConnected) {
    const finishedId = sessionId.value ?? selectedSessionId.value;
    // Whether navigateTo below actually changes route — a resumed call
    // returns to the same conversation it started on, which Vue Router
    // reuses (no remount) rather than re-navigating to. A brand-new
    // conversation's id is a route that's never been visited before, so
    // its own page mounts SelectedConversationTranscript fresh and it
    // fetches everything on its own; nothing extra needed there.
    const returningToSameConversation = finishedId === selectedSessionId.value;
    await navigateTo(finishedId ? `/c/${finishedId}` : "/");
    await sidebarRef.value?.refresh();
    // Landing back on the same conversation it started on doesn't change
    // the route param, so SelectedConversationTranscript's own watcher
    // never fires here — this is what actually picks up the messages the
    // call just added.
    if (returningToSameConversation) transcriptRef.value?.refresh();
  }
});

const { locale, locales, t, setLocale } = useI18n();
</script>

<template>
  <!-- v-app's own wrapper (node_modules/vuetify's .v-application__wrap)
    only sets min-height: 100dvh, a floor, not a ceiling — content taller
    than the viewport was growing the whole page instead of scrolling
    inside the sidebar/main panes that already had their own overflow
    rules. This shell is the actual ceiling: exactly one viewport tall,
    so the toolbar stays put and only .app-body's two children scroll. -->
  <div class="app-shell">
    <v-toolbar
      flat
      density="comfortable"
      class="lattice-surface"
      style="--lattice-color: rgba(43, 36, 28, 0.05)"
    >
      <!-- d-flex d-md-none, not a hand-written display:none + media query
      (see the language label below) — only meaningful once the sidebar
      becomes an off-canvas drawer at Vuetify's own md breakpoint
      (ConversationSidebar.vue's fixed-position switch matches it). -->
      <button
        type="button"
        class="menu-toggle d-flex d-md-none"
        :aria-label="t('openMenu')"
        :aria-expanded="sidebarOpen"
        @click="sidebarOpen = !sidebarOpen"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 20 20"
          fill="none"
          aria-hidden="true"
        >
          <line x1="3" y1="6" x2="17" y2="6" />
          <line x1="3" y1="10" x2="17" y2="10" />
          <line x1="3" y1="14" x2="17" y2="14" />
        </svg>
      </button>

      <v-toolbar-title class="font-weight-medium">
        {{ t("title") }}
      </v-toolbar-title>

      <!-- d-none d-sm-inline, not useDisplay() (see nuxt.config.ts) —
      pure CSS breakpoint, no hydration risk. -->
      <span class="d-none d-sm-inline text-body-2 me-2">
        {{ t("language") }}:
      </span>

      <!-- setLocale(), not v-model on locale — direct assignment skips
      translation loading, hooks, and the cookie (i18n lang-switcher docs). -->
      <v-btn-toggle
        :model-value="locale"
        mandatory
        density="compact"
        class="me-2"
        @update:model-value="setLocale"
      >
        <v-btn v-for="l in locales" :key="l.code" :value="l.code" size="small">
          {{ l.code.toUpperCase() }}
        </v-btn>
      </v-btn-toggle>
    </v-toolbar>

    <div class="app-body">
      <ConversationSidebar
        ref="sidebarRef"
        :selected-id="selectedSessionId"
        v-model:open="sidebarOpen"
        :disabled="connecting || connected"
        @select="selectConversation"
        @select-new="selectNewConversation"
      />

      <v-main
        class="lattice-surface main-content thin-scrollbar d-flex"
        style="--lattice-color: rgba(43, 36, 28, 0.035)"
      >
        <v-container
          fluid
          class="text-center d-flex flex-column justify-center main-container"
          style="min-height: min-content"
        >
          <!-- Not gated on !ready — resuming a past conversation into a
          live call used to hide this the instant the call connected,
          confirmed live: the whole point of "continue" is the old
          context staying visible, not vanishing the moment new turns
          start arriving. The live call's own turns now render INSIDE
          this same component (live-entries/awaiting-reply below) rather
          than in a separate TranscriptPanel underneath it — two adjacent
          boxes read as two conversations, one bolted after the other,
          not one continuing thread (confirmed live, twice: first the gap
          between them, then the seam itself once the gap was closed).
          The order-with-controls class (not a v-if move — that would
          remount this and lose its fetched messages/scroll position) is
          what keeps it below the mic controls once they appear. -->
          <SelectedConversationTranscript
            v-if="selectedSessionId"
            ref="transcriptRef"
            class="order-with-controls"
            :class="{ 'order-with-controls--active': ready }"
            :session-id="selectedSessionId"
            :live-entries="transcript"
            :awaiting-reply="awaitingReply"
            @not-found="conversationNotFound = true"
          />
          <!-- Signature interaction: octagonal "aperture" (the lattice's
          8-point construction), not a rounded pill — direction as form.
          Stays the visible state through BOTH network-connecting and
          waiting-for-the-agent-to-actually-greet — the room being
          technically connected doesn't mean anything's ready yet, and
          revealing the real controls (Stop especially) that early is
          what let a real early "hello" reach the room before the agent
          had ever spoken (see useSarjyRoom.ts connect()). Resumes the
          route's selected conversation when there is one, otherwise
          starts fresh — same button, same label, either way. Hidden
          rather than offered when the selected conversation turned out
          not to exist — nothing sensible to resume. -->
          <button
            v-if="!ready && !conversationNotFound"
            class="aperture-button main-narrow"
            type="button"
            :disabled="connecting || connected"
            @click="startOrResume"
          >
            {{
              connecting
                ? t("connecting")
                : connected
                  ? t("preparing")
                  : t("start")
            }}
          </button>
          <!-- The recovery path for the state above: the aperture button
          itself stays hidden here (nothing sensible to resume), so
          without this a bad/stale conversation link — someone else's id,
          a deleted conversation, a stale bookmark — left the not-found
          message showing with no way forward except the sidebar, which
          is itself hidden off-canvas on mobile. Reuses the sidebar's own
          "new conversation" wording/action so it reads as the same
          affordance, not a second, different one. -->
          <button
            v-if="conversationNotFound"
            class="aperture-button main-narrow"
            type="button"
            @click="selectNewConversation"
          >
            {{ t("newConversation") }}
          </button>
          <!-- Escape hatch, not the default path: "preparing" hanging is
          real, not hypothetical (today's TTS-provider outage left the
          greeting never playing at all) — past READY_TIMEOUT_MS with no
          greeting, this surfaces instead of leaving a silent, stuck
          spinner with no way out except closing the tab. -->
          <button
            v-if="slowToStart"
            class="audio-blocked-banner mt-4 main-narrow"
            type="button"
            @click="disconnect"
          >
            {{ t("slowToStart") }}
          </button>
          <p v-if="connectError" class="connect-error main-narrow" role="alert">
            {{
              connectError === "mic-denied"
                ? t("micDenied")
                : t("connectFailed")
            }}
          </p>
          <div v-if="ready" class="mb-6 order-controls main-narrow">
            <!-- Browsers can silently block audio autoplay even after the
            Start-talking click (useSarjyRoom.ts audioBlocked) — without
            this, Sarjy would be speaking with no indication anything's
            wrong: the transcript updates live, nothing is heard. -->
            <button
              v-if="audioBlocked"
              class="audio-blocked-banner"
              type="button"
              @click="resumeAudio"
            >
              {{ t("audioBlocked") }}
            </button>
            <VoiceLevelMeter
              :level="micLevel"
              :state="conversationState"
              :muted="muted"
              class="mb-4"
            />
            <div class="d-flex justify-center ga-3">
              <!-- No separate guard needed on Mute anymore — this whole
              block only renders once `ready` is true, which already
              implies the agent has greeted, so the entire loading phase
              (not just this one button) stays hidden until then. -->
              <button
                class="control-button"
                type="button"
                :aria-pressed="muted"
                @click="toggleMute"
              >
                <!-- Authored line icon, not a system icon font — none is
                wired into this project, and the app's whole language is
                hand-drawn geometry (the octagon cuts), not Material
                defaults. Mic body + stand, one consistent stroke; the
                slash is the actual state signal, always solid red
                regardless of hover/focus so it reads at a glance. -->
                <svg
                  class="mute-icon"
                  width="18"
                  height="18"
                  viewBox="0 0 20 20"
                  fill="none"
                  aria-hidden="true"
                >
                  <rect x="7.5" y="2.5" width="5" height="9" rx="2.5" />
                  <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0" />
                  <line x1="10" y1="15" x2="10" y2="17.5" />
                  <line x1="6.5" y1="17.5" x2="13.5" y2="17.5" />
                  <line
                    v-if="muted"
                    class="mute-icon__slash"
                    x1="3"
                    y1="3"
                    x2="17"
                    y2="17"
                  />
                </svg>
                {{ muted ? t("unmute") : t("mute") }}
              </button>
              <button class="control-button" type="button" @click="disconnect">
                {{ t("stop") }}
              </button>
            </div>
          </div>

          <!-- Only for a brand-new (non-resumed) conversation now — a
          resumed one shows its live turns inside SelectedConversationTranscript
          itself (live-entries/awaiting-reply above), so the two never
          render at once and this and that component's own "order" CSS
          never actually compete for the same slot in practice. -->
          <TranscriptPanel
            v-if="ready && !selectedSessionId"
            :entries="transcript"
            :awaiting-reply="awaitingReply"
            class="order-transcript-panel mt-6"
          />

          <LatencyHud
            :stages="latencyStages"
            :percentiles="latencyPercentiles"
            class="mt-8 order-latency main-narrow"
          />
        </v-container>
      </v-main>
    </div>
  </div>
</template>

<style scoped>
/* v-container is flex-column (Vuetify's own d-flex class) — these
   reorder its direct children visually, independent of DOM/mount order,
   specifically so SelectedConversationTranscript can stay mounted
   continuously (never v-if'd in and out, which would refetch it and
   lose scroll position) while still visually landing next to
   TranscriptPanel instead of above the mic controls. Everything else
   stays at the flex default (order: 0), so the idle/pre-call screens
   (aperture button, slowToStart, connectError) are untouched — this
   only matters once the controls/TranscriptPanel/LatencyHud all exist
   at once. */
.order-controls {
  order: 1;
}

.order-with-controls--active {
  order: 2;
}

.order-transcript-panel {
  order: 3;
}

.order-latency {
  order: 4;
}

.app-shell {
  display: flex;
  flex-direction: column;
  /* dvh, not vh — accounts for mobile browser chrome (address bar etc.)
     the way a bare 100vh doesn't, so this doesn't overshoot the real
     visible viewport on a phone. */
  height: 100dvh;
  overflow: hidden;
}

.app-body {
  display: flex;
  align-items: stretch;
  flex: 1;
  /* The flexbox default (min-height: auto) lets a flex item refuse to
     shrink below its content's natural size, which would silently defeat
     every overflow:auto below it — this is what actually lets the
     sidebar and main pane scroll inside a fixed-height row instead of
     pushing it taller. */
  min-height: 0;
  overflow: hidden;
}

.main-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

/* Overrides Vuetify's justify-center (plain justify-content: center, no
   !important, so a later same-specificity rule already wins — this adds
   safe purely for correctness). Plain center on a flex container whose
   content ends up taller than .main-content clips overflow equally from
   both ends, hiding the true start behind unreachable negative scroll
   instead of just scrolling to it; safe center falls back to start
   alignment exactly when centering would do that, and centers normally
   otherwise (idle screen, a short past conversation). */
/* No max-width of its own — SelectedConversationTranscript/TranscriptPanel
   (the actual messages) should use the main pane's real width, not be
   squeezed into a narrow column (confirmed live: 32rem read as cramped
   once real conversation content, not just a button, filled it). Every
   OTHER child (the aperture button, controls, latency, error banners)
   opts back into a centered, readable width itself via .main-narrow
   below — width stays scoped to what actually benefits from it, not
   the whole pane at once. */
.main-container {
  justify-content: safe center;
}

.main-narrow {
  width: 100%;
  max-width: 32rem;
  margin-inline: auto;
}

/* Visibility itself is d-flex d-md-none in the template (Vuetify's own
   utility classes, same convention as the language label's d-sm-inline
   below) — this only styles the icon once it's shown. */
.menu-toggle {
  align-items: center;
  justify-content: center;
  width: 2.25rem;
  height: 2.25rem;
  margin-inline-end: 0.5rem;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--ink-muted);
}

.menu-toggle:hover {
  color: rgb(var(--v-theme-primary));
}

.menu-toggle line {
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
}

.aperture-button {
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
  padding: 1.25rem 2.5rem;
  min-width: 12rem;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 1rem;
  font-weight: 600;
  color: rgb(var(--v-theme-surface));
  background-color: rgb(var(--v-theme-primary));
  border: none;
  cursor: pointer;
  transition: transform 0.15s ease;
}

.aperture-button:hover {
  transform: scale(1.03);
}

/* clip-path clips native outline/box-shadow entirely — drop-shadow()
   follows the clipped silhouette instead, giving a real ring. */
.aperture-button:focus-visible {
  outline: none;
  filter: drop-shadow(2px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(-2px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(0 2px 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(0 -2px 0 rgb(var(--v-theme-on-surface)));
}

.aperture-button:active {
  transform: scale(0.98);
}

.aperture-button:disabled {
  cursor: default;
  opacity: 0.6;
  transform: none;
}

/* Secondary controls, same octagon-cut family as .aperture-button
   (and VoiceLevelMeter's cells) at a smaller scale — outlined/ghost
   here since these are secondary actions, not the primary CTA. */
.control-button {
  clip-path: polygon(
    16% 0%,
    84% 0%,
    100% 16%,
    100% 84%,
    84% 100%,
    16% 100%,
    0% 84%,
    0% 16%
  );
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 1.25rem;
  min-width: 6rem;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-muted);
  background-color: transparent;
  border: 1.5px solid var(--ink-muted);
  cursor: pointer;
  transition:
    color 0.15s ease,
    border-color 0.15s ease,
    transform 0.15s ease;
}

.control-button:hover {
  color: rgb(var(--v-theme-primary));
  border-color: rgb(var(--v-theme-primary));
}

.control-button:focus-visible {
  outline: none;
  filter: drop-shadow(1.5px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(-1.5px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(0 1.5px 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(0 -1.5px 0 rgb(var(--v-theme-on-surface)));
}

.control-button:active {
  transform: scale(0.96);
}

.mute-icon {
  flex-shrink: 0;
}

.mute-icon rect,
.mute-icon path,
.mute-icon line {
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
}

/* The state signal, not decoration — stays solid error-red regardless
   of the button's own hover/focus color, so "muted" reads the same
   whether or not you're pointing at it. */
.mute-icon__slash {
  stroke: var(--state-error) !important;
}

.connect-error {
  margin-block-start: 1rem;
  padding: 0.5rem 1rem;
  border: 1px solid var(--state-error);
  color: var(--state-error);
  font-size: 0.85rem;
}

.audio-blocked-banner {
  display: block;
  width: 100%;
  margin-block-end: 1rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--state-error);
  background-color: transparent;
  color: var(--state-error);
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.audio-blocked-banner:hover {
  background-color: rgba(162, 59, 46, 0.08);
}

.audio-blocked-banner:focus-visible {
  outline: 2px solid var(--state-error);
  outline-offset: 2px;
}
</style>
