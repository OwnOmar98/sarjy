<!--
  Persistent conversation nav — same information architecture as
  claude.ai's sidebar (new conversation at top, past ones listed below,
  one always selected or none), built in this app's own lattice
  vocabulary rather than borrowing that product's look: octagon-cut rows
  and the same warm-sand/walnut-ink/brass materials as every other panel
  on this page. Selection is a route, not local state — selectedId comes
  down from the URL (app/pages/c/[id].vue) so a refresh stays on the same
  conversation; this component only ever asks to navigate, never owns
  "which one is selected" itself.
-->
<script setup lang="ts">
const props = defineProps<{
  selectedId: string | null;
  disabled: boolean;
  open: boolean;
}>();
const emit = defineEmits<{
  select: [id: string];
  "select-new": [];
  "update:open": [value: boolean];
}>();

const { getOrCreateIdentity } = useSarjyRoom();
const { t, locale } = useI18n();

// SessionSummary comes from shared/types/conversation.ts (Nuxt's shared/
// layer, auto-imported both app- and server-side) — the same shape
// server/api/sessions.get.ts's rows and a live "session-upserted" push
// both already are, so upsertSession below never has to reshape either.

interface SessionsPage {
  items: SessionSummary[];
  nextCursor: string | null;
}

type LoadDone = (status: "ok" | "empty" | "error") => void;

const sessions = ref<SessionSummary[]>([]);
const cursor = ref<string | null>(null);
// Distinct from v-infinite-scroll's own load/empty status — that status
// covers "no *more* pages," which fires just as validly after 40 real
// conversations already loaded as it does on a genuinely empty account.
// This is specifically "the very first page came back," which gates the
// skeleton vs. the real "you have zero conversations" message.
const hasLoadedOnce = ref(false);
const infiniteScrollRef = ref<{ reset: (side?: string) => void } | null>(null);

// Set by refresh() right before it re-arms v-infinite-scroll, so the very
// next onLoad cycle knows to keep paging past just one page. Read once by
// onLoad and cleared immediately — v-infinite-scroll only exposes reset(),
// not a way to set its internal status directly, so the *only* way to page
// through several fetches while keeping that status correct is to do the
// paging inside one onLoad call and call the real done() once at the end,
// not to call reset() repeatedly from out here (each call after the list
// is already fully caught up would re-fetch page 1 with no cursor and
// duplicate it — cursor:null means "start of the list", not "the end").
let catchUpTarget: { count: number; mustInclude: string | null } | null = null;

async function onLoad({ done }: { done: LoadDone }) {
  const target = catchUpTarget;
  catchUpTarget = null;
  try {
    let status: "ok" | "empty" = "empty";
    do {
      const page = await $fetch<SessionsPage>("/api/sessions", {
        query: {
          identity: getOrCreateIdentity(),
          cursor: cursor.value ?? undefined,
        },
      });
      // A live "session-upserted" push (see upsertSession below) can land
      // while this exact page is still in flight — e.g. the very first
      // load, or a reconnect catch-up racing a push that arrives right
      // after the socket reopens. Without this filter that session would
      // appear twice: once from upsertSession, once from this page.
      const alreadyLoaded = new Set(sessions.value.map((s) => s.id));
      sessions.value.push(
        ...page.items.filter((s) => !alreadyLoaded.has(s.id)),
      );
      cursor.value = page.nextCursor;
      status = page.nextCursor ? "ok" : "empty";
      // A brand-new conversation from elsewhere shifts everything below it
      // down by one, so "loaded as many as before" alone can land just
      // short of the previously-visible active one — checked every lap,
      // not just once, since either target can need more than one page.
    } while (
      status === "ok" &&
      target !== null &&
      (sessions.value.length < target.count ||
        (target.mustInclude !== null &&
          !sessions.value.some((s) => s.id === target.mustInclude)))
    );
    done(status);
  } catch {
    // Not fatal — the app is fully usable without history; a fresh
    // conversation with no memory of past ones is the same experience
    // this had before the feature existed.
    done("error");
  } finally {
    hasLoadedOnce.value = true;
  }
}

// Called for every "session-upserted" live push (SarjyApp.vue) — a new
// conversation appearing, or an existing one's summary/updated_at
// changing. Always placed at the top rather than sorted in by
// updated_at: both moments this fires (conversations.py's start_session
// and end_session) set updated_at to essentially now, so the pushed
// session is — by construction — never anything but the most recently
// active one at the instant it arrives.
function upsertSession(session: SessionSummary) {
  const existingIndex = sessions.value.findIndex((s) => s.id === session.id);
  if (existingIndex !== -1) sessions.value.splice(existingIndex, 1);
  sessions.value.unshift(session);
  hasLoadedOnce.value = true;
}

// Reconciliation after the WebSocket reconnects (useLiveUpdates.ts's
// onReconnect) — not called on every live push anymore now that
// upsertSession above patches the list directly, only when the socket
// was actually disconnected for a stretch and may have missed pushes
// that happened during the gap. Preserves whatever was already loaded
// (and the active conversation specifically, wherever it ends up
// landing) instead of collapsing back to a single page.
function refresh() {
  catchUpTarget = {
    count: sessions.value.length,
    mustInclude: props.selectedId,
  };
  sessions.value = [];
  cursor.value = null;
  hasLoadedOnce.value = false;
  // reset() re-arms v-infinite-scroll's own status (loading a fresh list
  // after it previously reached "empty"/no-more-pages would otherwise be
  // a no-op — see node_modules/vuetify's own VInfiniteScroll.js) and,
  // for intersect mode, re-fires the load cycle itself rather than
  // waiting for a real scroll/visibility change that may never happen.
  infiniteScrollRef.value?.reset("end");
}

defineExpose({ refresh, upsertSession });

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(locale.value, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function selectNew() {
  if (props.disabled) return;
  emit("select-new");
  emit("update:open", false);
}

function select(id: string) {
  if (props.disabled) return;
  emit("select", id);
  emit("update:open", false);
}
</script>

<template>
  <!-- Backdrop only exists (and only intercepts clicks) on the narrow
    layout, where the sidebar is an off-canvas drawer over the content
    rather than a permanent column beside it. -->
  <div
    v-if="open"
    class="sidebar-backdrop"
    @click="emit('update:open', false)"
  />
  <aside class="sidebar lattice-surface" :class="{ 'sidebar--open': open }">
    <button
      type="button"
      class="sidebar__new"
      :disabled="disabled"
      :aria-current="selectedId === null"
      @click="selectNew"
    >
      <svg
        class="sidebar__new-icon"
        width="16"
        height="16"
        viewBox="0 0 20 20"
        fill="none"
        aria-hidden="true"
      >
        <line x1="10" y1="4" x2="10" y2="16" />
        <line x1="4" y1="10" x2="16" y2="10" />
      </svg>
      {{ t("newConversation") }}
    </button>

    <h2 class="sidebar__heading">{{ t("pastConversations") }}</h2>

    <!-- v-infinite-scroll owns the whole pagination lifecycle, including
      the first page — mode="intersect" fires @load as soon as its
      sentinel is visible, which it is the instant this mounts with
      nothing loaded yet. Its own loading/empty status is "no *more*
      pages," a different thing from hasLoadedOnce/sessions.length below
      (that's "zero conversations ever"), so those live in the default
      slot, not the #loading/#empty ones. -->
    <v-infinite-scroll
      ref="infiniteScrollRef"
      mode="intersect"
      side="end"
      class="sidebar__scroll thin-scrollbar"
      @load="onLoad"
    >
      <ul v-if="!hasLoadedOnce" class="sidebar__list" aria-hidden="true">
        <li v-for="i in 4" :key="i" class="sidebar__skeleton-item">
          <span class="sidebar__skeleton-bar sidebar__skeleton-bar--text" />
          <span
            class="sidebar__skeleton-bar sidebar__skeleton-bar--text"
            :style="{ inlineSize: i % 2 ? '70%' : '50%' }"
          />
          <span class="sidebar__skeleton-bar sidebar__skeleton-bar--date" />
        </li>
      </ul>

      <p v-else-if="!sessions.length" class="sidebar__empty">
        {{ t("pastConversationsEmpty") }}
      </p>

      <ul v-else class="sidebar__list">
        <li v-for="s in sessions" :key="s.id">
          <button
            type="button"
            class="sidebar__item"
            :class="{ 'sidebar__item--active': selectedId === s.id }"
            :disabled="disabled"
            :aria-current="selectedId === s.id"
            @click="select(s.id)"
          >
            <span class="sidebar__item-summary">
              {{ s.summary || t("pastConversationsNoSummary") }}
            </span>
            <span class="sidebar__item-date">{{
              formatDate(s.updated_at)
            }}</span>
          </button>
        </li>
      </ul>

      <template #loading>
        <div class="sidebar__skeleton-item" aria-hidden="true">
          <span class="sidebar__skeleton-bar sidebar__skeleton-bar--text" />
          <span
            class="sidebar__skeleton-bar sidebar__skeleton-bar--text"
            style="inline-size: 60%"
          />
          <span class="sidebar__skeleton-bar sidebar__skeleton-bar--date" />
        </div>
      </template>
      <!-- Pagination genuinely exhausted, not "zero conversations" (that
        case is the sidebar__empty branch above) — nothing to say here. -->
      <template #empty />
    </v-infinite-scroll>
  </aside>
</template>

<style scoped>
.sidebar-backdrop {
  position: fixed;
  inset: 0;
  background-color: rgba(43, 36, 28, 0.35);
  z-index: 20;
}

.sidebar {
  --lattice-color: rgba(43, 36, 28, 0.045);
  /* lattice-surface (main.css) only paints the pattern texture as an
     overlay — every other user of that class (v-toolbar, v-main) already
     sits on Vuetify's own opaque color underneath it. This is a plain
     element with none, so on the fixed/off-canvas drawer the page behind
     it showed straight through the pattern. background, not surface —
     surface (#FBF8F2) is a touch lighter/whiter than the app's actual
     ground color (background, #F2EDE3, the same token v-app/v-main sit
     on by default), so the sidebar read as a flat white panel dropped
     onto the warmer page instead of the same material continuing
     underneath it — confirmed live. */
  background-color: rgb(var(--v-theme-background));
  display: flex;
  flex-direction: column;
  width: 17rem;
  flex-shrink: 0;
  height: 100%;
  padding: 1rem 0.75rem;
  border-inline-end: 1px solid rgba(43, 36, 28, 0.16);
  /* No overflow here — v-infinite-scroll (.sidebar__scroll) owns its own
     scroll region below, so "New conversation" and the heading stay
     pinned above it instead of scrolling away with the list. */
  overflow: hidden;
  text-align: start;
}

.sidebar__scroll {
  flex: 1;
  min-height: 0;
}

.sidebar__new {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.65rem 0.85rem;
  margin-block-end: 1.25rem;
  clip-path: polygon(
    8% 0%,
    92% 0%,
    100% 8%,
    100% 92%,
    92% 100%,
    8% 100%,
    0% 92%,
    0% 8%
  );
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  font-size: 0.85rem;
  font-weight: 600;
  color: rgb(var(--v-theme-surface));
  background-color: rgb(var(--v-theme-primary));
  border: none;
  cursor: pointer;
  transition:
    transform 0.15s ease,
    opacity 0.15s ease;
}

.sidebar__new:hover:not(:disabled) {
  transform: scale(1.02);
}

.sidebar__new:disabled {
  cursor: default;
  opacity: 0.5;
}

.sidebar__new-icon line {
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
}

.sidebar__heading {
  padding-inline: 0.35rem;
  margin-block-end: 0.4rem;
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ink-muted);
}

.sidebar__empty {
  padding: 0.5rem 0.35rem;
  font-size: 0.8rem;
  line-height: 1.4;
  color: var(--ink-muted);
}

/* Same footprint as a real .sidebar__item — same padding and octagon
   clip-path, and the two text bars sized/spaced to fill the same
   two-line-clamped summary block the real card shows, not thin unrelated
   ticks — so this reads as "the card, loading" and nothing jumps in
   height when real rows replace it. */
.sidebar__skeleton-item {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0.5rem 0.6rem;
  clip-path: polygon(
    5% 0%,
    95% 0%,
    100% 5%,
    100% 95%,
    95% 100%,
    5% 100%,
    0% 95%,
    0% 5%
  );
}

.sidebar__skeleton-bar {
  display: block;
  block-size: 0.7rem;
  background-color: rgba(43, 36, 28, 0.1);
  animation: sidebar-skeleton-pulse 1.4s ease-in-out infinite;
}

.sidebar__skeleton-bar--text {
  inline-size: 100%;
}

.sidebar__skeleton-bar--date {
  block-size: 0.5rem;
  inline-size: 35%;
  margin-block-start: 0.2rem;
}

/* Same cascading-delay idea as TranscriptPanel.vue's thinking cells, one
   authored pulse rather than a shimmer sweep this design has nowhere
   else. */
.sidebar__skeleton-item:nth-child(2) {
  animation-delay: 0.1s;
}

.sidebar__skeleton-item:nth-child(3) {
  animation-delay: 0.2s;
}

.sidebar__skeleton-item:nth-child(4) {
  animation-delay: 0.3s;
}

@keyframes sidebar-skeleton-pulse {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sidebar__skeleton-bar {
    animation: none;
  }
}

.sidebar__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.sidebar__item {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  width: 100%;
  padding: 0.5rem 0.6rem;
  clip-path: polygon(
    5% 0%,
    95% 0%,
    100% 5%,
    100% 95%,
    95% 100%,
    5% 100%,
    0% 95%,
    0% 5%
  );
  background: none;
  border: none;
  cursor: pointer;
  text-align: start;
  font-family: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
  color: inherit;
  transition: background-color 0.15s ease;
}

.sidebar__item:hover:not(:disabled) {
  background-color: rgba(176, 122, 34, 0.08);
}

.sidebar__item:disabled {
  cursor: default;
  opacity: 0.5;
}

/* The signature accent doing selection duty, not a generic highlight —
   same brass this app already spends on its one committed color. */
.sidebar__item--active {
  background-color: rgba(176, 122, 34, 0.14);
}

.sidebar__item-summary {
  font-size: 0.82rem;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.sidebar__item-date {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.65rem;
  font-variant-numeric: tabular-nums;
  color: var(--ink-muted);
}

/* Persistent column at rest; off-canvas drawer only below Vuetify's own
   md breakpoint (840px, checked in node_modules/vuetify — not configured
   anywhere in this project, so this is Vuetify's real default, not an
   arbitrary number) — position/transform can't be expressed with
   Vuetify's d-* utility classes (those only toggle display), so this one
   media query stays hand-written, but on the app's real breakpoint scale
   rather than a one-off value. SarjyApp.vue's menu-toggle matches it via
   d-md-none. */
@media (max-width: 839px) {
  .sidebar {
    position: fixed;
    inset-block: 0;
    /* Resolves to the correct physical edge per direction on its own
       (left in LTR, right in RTL) — no [dir="rtl"] override needed for
       position itself, only for the transform/shadow below. */
    inset-inline-start: 0;
    z-index: 21;
    width: 18rem;
    max-width: 82vw;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
    box-shadow: 4px 0 16px rgba(43, 36, 28, 0.18);
  }

  /* transform/box-shadow are physical, not logical — flipped by hand. */
  [dir="rtl"] .sidebar {
    transform: translateX(100%);
    box-shadow: -4px 0 16px rgba(43, 36, 28, 0.18);
  }

  /* .sidebar.sidebar--open, not plain .sidebar--open — needs to match
     [dir="rtl"] .sidebar's specificity or the RTL closed-position rule
     always wins and the drawer never opens in Arabic. */
  .sidebar.sidebar--open {
    transform: translateX(0);
  }
}
</style>
