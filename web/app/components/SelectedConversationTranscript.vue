<!--
  Read-only playback of a past conversation, shown in the main pane when
  the sidebar has one selected. Same log-not-bubbles block TranscriptPanel
  uses for the live conversation — a past one and a live one read as the
  same kind of thing, not two different components wearing different
  clothes.
-->
<script setup lang="ts">
import type { TranscriptEntry } from "~/composables/useSarjyRoom";

const props = withDefaults(
  defineProps<{
    sessionId: string;
    // The live call's own turns, rendered as a continuation of the same
    // scroll box's history rather than in a separate TranscriptPanel
    // below it — a resumed conversation reads as one thread, not a past
    // card followed by a live one.
    liveEntries?: TranscriptEntry[];
    awaitingReply?: boolean;
  }>(),
  { liveEntries: () => [], awaitingReply: false },
);
const emit = defineEmits<{ "not-found": [] }>();
const { getOrCreateIdentity } = useSarjyRoom();
const { t, locale } = useI18n();

// Absolute clock time, not TranscriptPanel.vue's elapsed-since-connect —
// these messages can span minutes or, once a conversation is reopened
// (agent/conversations.py's resume flow), real days, so "3:47" as a
// countdown from a connect time that no longer means anything wouldn't
// be readable the way it is for a single live call.
function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(locale.value, {
    hour: "numeric",
    minute: "2-digit",
  });
}

// TranscriptPanel.vue's own clock, for the live entries rendered inline
// below — elapsed-since-connect, not the absolute clock time history
// above uses, since a live entry has no created_at of its own yet.
function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

// TranscriptMessage comes from shared/types/conversation.ts (Nuxt's
// shared/ layer) — the same shape server/api/sessions/[id]/messages.get.ts's
// rows and a live "message-added" push both already are. Aliased locally
// so every other reference in this file (and the template) didn't need
// renaming.
type Message = TranscriptMessage;

interface MessagesPage {
  items: Message[];
  nextCursor: string | null;
}

type LoadDone = (status: "ok" | "empty" | "error") => void;

const messages = ref<Message[]>([]);
const cursor = ref<string | null>(null);
// Distinct from v-infinite-scroll's own load status (see
// ConversationSidebar.vue's identical reasoning) — "no more pages" is
// not "hasn't loaded at all yet."
const hasLoadedOnce = ref(false);
// Distinct from an empty-but-real conversation (0 messages is valid —
// e.g. a session that opened and closed without anyone saying anything)
// — this is specifically "no session with this id belongs to you",
// which the API's ownership check (web/server/api/sessions/[id]/
// messages.get.ts) already reports as a 404, e.g. a stale bookmark, a
// deleted conversation, or a URL for someone else's session id.
const notFound = ref(false);
const infiniteScrollRef = ref<{
  reset: (side?: string) => void;
  $el: HTMLElement;
} | null>(null);
const showScrollToBottom = ref(false);
const SCROLL_BOTTOM_THRESHOLD_PX = 80;

// v-infinite-scroll's own mode="intersect" load cycle is what's SSR-safe
// here, not a manual onMounted call — its trigger is a real
// IntersectionObserver, which (like onMounted) only exists client-side,
// so this never runs during SSR the way an immediate watch's callback
// would. That distinction is exactly what a prior bug turned on:
// getOrCreateIdentity() reads localStorage, which doesn't exist on the
// server; an immediate watch ran it at setup time, including
// server-side, hit that ReferenceError, got silently swallowed by a
// catch, and rendered an empty transcript on the server while the client
// rendered the real messages — a genuine hydration mismatch, confirmed
// live. v-infinite-scroll's first page load never has that problem.
//
// side="start": this opens like a chat, scrolled to the newest message,
// with *older* history loading as you scroll up — the opposite of
// sessions.get.ts's list, which pages forward through older
// conversations. The API already returns each page oldest-first, so
// every page (including the first) is unshifted onto the front, not
// pushed onto the end.
//
// Set by refresh() right before it re-arms v-infinite-scroll — same
// reasoning as ConversationSidebar.vue's identical catchUpTarget: paging
// through several fetches while keeping v-infinite-scroll's own status
// correct means doing all of it inside one onLoad call and calling the
// real done() once at the end, not calling reset() repeatedly from out
// here.
let catchUpTarget: number | null = null;

async function onLoad({ done }: { done: LoadDone }) {
  const target = catchUpTarget;
  catchUpTarget = null;
  try {
    let status: "ok" | "empty" = "empty";
    do {
      const page = await $fetch<MessagesPage>(
        `/api/sessions/${props.sessionId}/messages`,
        {
          query: {
            identity: getOrCreateIdentity(),
            cursor: cursor.value ?? undefined,
          },
        },
      );
      // A live "message-added" push (see appendMessage below) can land
      // while this exact page is still in flight — same race as
      // ConversationSidebar.vue's identical guard.
      const alreadyLoaded = new Set(messages.value.map((m) => m.id));
      messages.value.unshift(
        ...page.items.filter((m) => !alreadyLoaded.has(m.id)),
      );
      cursor.value = page.nextCursor;
      status = page.nextCursor ? "ok" : "empty";
    } while (
      status === "ok" &&
      target !== null &&
      messages.value.length < target
    );
    done(status);
  } catch (err) {
    if (
      !!err &&
      typeof err === "object" &&
      "response" in err &&
      (err as { response?: { status?: number } }).response?.status === 404
    ) {
      notFound.value = true;
      emit("not-found");
    }
    done("error");
  } finally {
    hasLoadedOnce.value = true;
  }
}

// Reconciliation after the WebSocket reconnects (useLiveUpdates.ts's
// onReconnect), a genuinely different conversation being opened, or this
// same tab's own call just ending — not called on every live push
// anymore now that appendMessage below patches the array directly, only
// when something might actually have been missed. Preserves as much
// history as was already loaded, the same reasoning as
// ConversationSidebar.vue's refresh().
function refresh() {
  catchUpTarget = messages.value.length;
  messages.value = [];
  cursor.value = null;
  hasLoadedOnce.value = false;
  notFound.value = false;
  infiniteScrollRef.value?.reset("start");
}

// Called for every "message-added" live push whose sessionId matches
// this conversation (SarjyApp.vue filters that before calling in —
// tabs looking at a *different* conversation ignore the push entirely).
// Always appended at the end: unlike a session, a message never needs
// re-sorting, it's simply the newest thing that's happened.
function appendMessage(message: Message) {
  if (messages.value.some((m) => m.id === message.id)) return;
  messages.value.push(message);
  // Same "don't yank the view if the reader scrolled into history" rule
  // as the liveEntries watcher below — a message arriving for this
  // conversation from elsewhere is exactly as disruptive to fight past
  // scroll-up as this tab's own live reply would be.
  if (hasLoadedOnce.value && !showScrollToBottom.value) {
    nextTick(() => scrollToBottom("smooth"));
  }
}

// This component stays mounted across a straight past-conversation-to-
// past-conversation navigation (Vue Router reuses it across a
// param-only route change) — a fresh sessionId means starting the whole
// page cycle over.
watch(() => props.sessionId, refresh);

// SarjyApp.vue calls this explicitly after a resumed call ends and
// navigates back to this same conversation's URL — navigating to the
// route it's already on doesn't change the sessionId prop, so the watch
// above never fires there, and without this the new messages the call
// just added stayed invisible until an unrelated navigation happened to
// remount the component (confirmed live).
defineExpose({ refresh, appendMessage });

function scrollContainer(): HTMLElement | null {
  // v-infinite-scroll's root element *is* the scrollable box (its own
  // CSS sets overflow-y: auto on itself) — $el is a template ref's
  // always-available escape hatch to a component's root DOM node, not
  // something VInfiniteScroll has to explicitly opt into exposing.
  return infiniteScrollRef.value?.$el ?? null;
}

function scrollToBottom(behavior: ScrollBehavior) {
  const el = scrollContainer();
  el?.scrollTo({ top: el.scrollHeight, behavior });
}

function updateScrollToBottomVisibility() {
  const el = scrollContainer();
  if (!el) return;
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  showScrollToBottom.value = distanceFromBottom > SCROLL_BOTTOM_THRESHOLD_PX;
}

// Only the very first page of a (re)opened conversation jumps to the
// bottom — v-infinite-scroll's own side="start" handling already
// preserves scroll position when an *older* page loads in from scrolling
// up (node_modules/vuetify's own VInfiniteScroll.js adjusts scrollTop by
// exactly the new content's height), so re-triggering a bottom-jump on
// every load here would fight that and yank the view down mid-read.
watch(hasLoadedOnce, async (loaded, wasLoaded) => {
  if (loaded && !wasLoaded) {
    await nextTick();
    scrollToBottom("instant");
    updateScrollToBottomVisibility();
  }
});

// New live turns land in this same box (see liveEntries above) — follow
// them the way TranscriptPanel.vue's own watcher used to, but only when
// the reader hasn't deliberately scrolled up into history; otherwise a
// live reply mid-scrollback would yank them back down to it.
watch(
  () => [props.liveEntries, props.awaitingReply],
  async () => {
    if (!hasLoadedOnce.value || showScrollToBottom.value) return;
    await nextTick();
    scrollToBottom("smooth");
  },
  { deep: true },
);

onMounted(() => {
  scrollContainer()?.addEventListener(
    "scroll",
    updateScrollToBottomVisibility,
    {
      passive: true,
    },
  );
});

onUnmounted(() => {
  scrollContainer()?.removeEventListener(
    "scroll",
    updateScrollToBottomVisibility,
  );
});
</script>

<template>
  <div class="selected-transcript">
    <div class="selected-transcript__scroll-wrap">
      <!-- v-infinite-scroll owns the whole pagination lifecycle including
        the first page, the same way ConversationSidebar.vue's does — its
        own #loading/#empty status means "no *more* pages," a different
        thing from hasLoadedOnce/notFound below, so those live in the
        default slot. side="start": opens scrolled to the newest message
        (see the script's onLoad comment), older history loads as you
        scroll up. -->
      <v-infinite-scroll
        ref="infiniteScrollRef"
        mode="intersect"
        side="start"
        class="transcript thin-scrollbar"
        @load="onLoad"
      >
        <!-- Same .transcript__row shell the real content renders in below,
        so the skeleton is "this card, loading" rather than a generic
        spinner that gets swapped for a differently-shaped result. -->
        <div v-if="!hasLoadedOnce" aria-hidden="true">
          <div
            v-for="i in 4"
            :key="i"
            class="transcript__row"
            :class="{ 'transcript__row--agent': i % 2 === 0 }"
          >
            <span class="transcript__meta">
              <span class="skeleton-bar skeleton-bar--speaker" />
            </span>
            <span
              class="skeleton-bar skeleton-bar--text"
              :style="{ inlineSize: i % 2 === 0 ? '85%' : '60%' }"
            />
          </div>
        </div>
        <p v-else-if="notFound" class="selected-transcript__not-found">
          {{ t("conversationNotFound") }}
        </p>
        <template v-else>
          <div
            v-for="m in messages"
            :key="m.id"
            class="transcript__row"
            :class="`transcript__row--${m.role === 'user' ? 'user' : 'agent'}`"
          >
            <span class="transcript__meta">
              <span class="transcript__speaker">
                {{ m.role === "user" ? t("you") : t("title") }}
              </span>
              <span class="transcript__time">{{
                formatTime(m.created_at)
              }}</span>
            </span>
            <span class="transcript__text">{{ m.content }}</span>
          </div>

          <!-- The live call's own turns, appended into this same box as
          they arrive — a resumed conversation reading as one continuous
          thread with the history above it, not a second card bolted on
          below. Same row/meta/text classes as history; only the time
          treatment differs (elapsed vs. absolute, see formatElapsed). -->
          <div
            v-for="entry in liveEntries"
            :key="entry.id"
            class="transcript__row"
            :class="`transcript__row--${entry.role === 'user' ? 'user' : 'agent'}`"
          >
            <span class="transcript__meta">
              <span class="transcript__speaker">
                {{ entry.role === "user" ? t("you") : t("title") }}
              </span>
              <span class="transcript__time">{{
                formatElapsed(entry.elapsedMs)
              }}</span>
            </span>
            <span
              class="transcript__text"
              :class="{ 'transcript__text--interim': !entry.final }"
              >{{ entry.text }}</span
            >
          </div>
          <div
            v-if="awaitingReply"
            class="transcript__row transcript__row--agent"
          >
            <span class="transcript__meta">
              <span class="transcript__speaker">{{ t("title") }}</span>
            </span>
            <span class="transcript__thinking" :aria-label="t('thinking')">
              <span v-for="i in 3" :key="i" class="transcript__thinking-cell" />
            </span>
          </div>
        </template>

        <template #loading>
          <div class="transcript__row" aria-hidden="true">
            <span class="transcript__meta">
              <span class="skeleton-bar skeleton-bar--speaker" />
            </span>
            <span
              class="skeleton-bar skeleton-bar--text"
              style="inline-size: 70%"
            />
          </div>
        </template>
        <!-- A real fetch failure (not-found is handled above, in the
        default slot, not here) is rare enough and non-fatal enough
        (the conversation just stops loading further messages) not to
        need its own message on top of the app's existing "not fatal"
        degradation philosophy. -->
        <template #error />
        <!-- Pagination genuinely exhausted, not "zero messages" (that's
        still a real, valid conversation state, shown via the empty
        v-else block above producing no rows). -->
        <template #empty />
      </v-infinite-scroll>

      <button
        v-if="showScrollToBottom"
        type="button"
        class="scroll-to-bottom"
        :aria-label="t('scrollToBottom')"
        @click="scrollToBottom('smooth')"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 20 20"
          fill="none"
          aria-hidden="true"
        >
          <line x1="10" y1="3" x2="10" y2="17" />
          <polyline points="4,11 10,17 16,11" />
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.selected-transcript {
  text-align: start;
  margin-block-end: 1.5rem;
}

.selected-transcript__scroll-wrap {
  position: relative;
}

/* Octagon-cut, brass-filled, same family as .aperture-button/.mute-icon
   in SarjyApp.vue and TranscriptPanel.vue's thinking cells — not a
   rounded Material FAB, which would be the one un-drawn, off-the-shelf
   shape on this whole page. */
.scroll-to-bottom {
  position: absolute;
  inset-inline-end: 0.75rem;
  bottom: 0.75rem;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
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
  background-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-surface));
  border: none;
  cursor: pointer;
  filter: drop-shadow(0 2px 4px rgba(43, 36, 28, 0.35));
  transition: transform 0.15s ease;
}

.scroll-to-bottom:hover {
  transform: scale(1.08);
}

.scroll-to-bottom:focus-visible {
  outline: none;
  filter: drop-shadow(0 2px 4px rgba(43, 36, 28, 0.35))
    drop-shadow(1.5px 0 0 rgb(var(--v-theme-on-surface)))
    drop-shadow(-1.5px 0 0 rgb(var(--v-theme-on-surface)));
}

.scroll-to-bottom svg {
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
  fill: none;
}

/* No border of its own — this now renders inside .transcript's already-
   bordered scroll box (v-infinite-scroll's default slot), not standing
   alone the way it did before pagination needed that wrapper. */
.selected-transcript__not-found {
  padding: 1rem;
  text-align: center;
  font-size: 0.85rem;
  color: var(--ink-muted);
}

.transcript {
  max-height: 18rem;
  overflow-y: auto;
  border: 1px solid rgba(43, 36, 28, 0.16);
}

.transcript__row {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid rgba(43, 36, 28, 0.1);
}

.transcript__row:last-child {
  border-bottom: none;
}

.transcript__row--agent {
  background-color: rgba(176, 122, 34, 0.05);
}

.transcript__meta {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
}

.transcript__speaker {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-muted);
}

/* Same token as TranscriptPanel.vue's live .transcript__time — one
   treatment for "when," absolute here vs. elapsed there. */
.transcript__time {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 0.65rem;
  font-variant-numeric: tabular-nums;
  color: var(--ink-muted);
}

.transcript__text {
  font-size: 0.9rem;
  line-height: 1.4;
  overflow-wrap: break-word;
}

/* Live entries only (see liveEntries prop) — an in-flight partial
   transcription reads as provisional, same treatment TranscriptPanel.vue
   used for its own live rows before they merged into this box. */
.transcript__text--interim {
  font-style: italic;
  color: var(--ink-muted);
}

.transcript__thinking {
  display: inline-flex;
  gap: 4px;
  padding-block-start: 0.2rem;
}

/* Same octagon-cut cell as .scroll-to-bottom/.aperture-button — brass,
   not a Material dot-typing indicator. */
.transcript__thinking-cell {
  width: 6px;
  height: 6px;
  background-color: rgb(var(--v-theme-primary));
  clip-path: polygon(
    20% 0%,
    80% 0%,
    100% 20%,
    100% 80%,
    80% 100%,
    20% 100%,
    0% 80%,
    0% 20%
  );
  animation: transcript-thinking 1s infinite ease-in-out;
}

.transcript__thinking-cell:nth-child(2) {
  animation-delay: 0.15s;
}

.transcript__thinking-cell:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes transcript-thinking {
  0%,
  80%,
  100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .transcript__thinking-cell {
    animation: none;
  }
}

/* Same pulse ConversationSidebar.vue's skeleton uses — one authored
   loading moment across the app, not a different technique per panel. */
.skeleton-bar {
  display: block;
  background-color: rgba(43, 36, 28, 0.1);
  animation: selected-transcript-skeleton-pulse 1.4s ease-in-out infinite;
}

.skeleton-bar--speaker {
  block-size: 0.55rem;
  inline-size: 3.5rem;
}

.skeleton-bar--text {
  block-size: 0.75rem;
  margin-block-start: 0.3rem;
}

.transcript__row:nth-child(2) .skeleton-bar {
  animation-delay: 0.1s;
}

.transcript__row:nth-child(3) .skeleton-bar {
  animation-delay: 0.2s;
}

.transcript__row:nth-child(4) .skeleton-bar {
  animation-delay: 0.3s;
}

@keyframes selected-transcript-skeleton-pulse {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-bar {
    animation: none;
  }
}
</style>
