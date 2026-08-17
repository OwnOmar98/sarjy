<!--
  Live turn-by-turn transcript (useSarjyRoom.ts transcript, awaitingReply)
  — a log, not a chat-bubble thread; app.vue's direction-contract
  explicitly refuses the rounded-bubble default. Same lattice-cell module
  as LatencyHud.vue and VoiceLevelMeter.vue.
-->
<script setup lang="ts">
import type { TranscriptEntry } from "~/composables/useSarjyRoom";

const props = defineProps<{
  entries: TranscriptEntry[];
  awaitingReply: boolean;
}>();
const { t } = useI18n();

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

const listEl = ref<HTMLElement>();
// deep, not [entries.length, awaitingReply] — most updates are an
// existing entry's .text growing in place as interim transcript comes
// in (useSarjyRoom.ts updates by segment id, it doesn't push a new
// array item for that), so length alone misses almost every update and
// the panel stops scrolling partway through a long turn.
watch(
  () => [props.entries, props.awaitingReply],
  async () => {
    await nextTick();
    listEl.value?.scrollTo({
      top: listEl.value.scrollHeight,
      behavior: "smooth",
    });
  },
  { deep: true },
);
</script>

<template>
  <div ref="listEl" class="transcript thin-scrollbar">
    <p v-if="!entries.length" class="transcript__empty">
      {{ t("transcriptEmpty") }}
    </p>
    <template v-else>
      <div
        v-for="entry in entries"
        :key="entry.id"
        class="transcript__row"
        :class="`transcript__row--${entry.role}`"
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
        >
          {{ entry.text }}
        </span>
      </div>
      <div v-if="awaitingReply" class="transcript__row transcript__row--agent">
        <span class="transcript__meta">
          <span class="transcript__speaker">{{ t("title") }}</span>
        </span>
        <span class="transcript__thinking" :aria-label="t('thinking')">
          <span v-for="i in 3" :key="i" class="transcript__thinking-cell" />
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.transcript {
  max-height: 16rem;
  overflow-y: auto;
  border: 1px solid rgba(43, 36, 28, 0.16);
  text-align: start;
}

.transcript__empty {
  padding: 1.5rem 1rem;
  text-align: center;
  font-size: 0.85rem;
  color: var(--ink-muted);
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

/* Interim (not-yet-final) text stays legible, not faded to gray — the
   distinction from a settled line is the italic, not illegibility. */
.transcript__text--interim {
  font-style: italic;
  color: var(--ink-muted);
}

.transcript__thinking {
  display: inline-flex;
  gap: 4px;
  padding-block-start: 0.2rem;
}

/* Same octagon module as VoiceLevelMeter's bars, not generic bouncing
   dots — the two live-status widgets share one visual vocabulary. */
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
</style>
