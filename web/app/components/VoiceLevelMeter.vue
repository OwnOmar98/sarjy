<!--
  Live mic level (useSarjyRoom.ts micLevel) plus the session's real
  conversation state (conversationState — driven only by room events,
  never inferred). Segmented octagon cells, not a rounded/glowing blob —
  same lattice module as LatencyHud.vue and TranscriptPanel.vue's
  thinking indicator.
-->
<script setup lang="ts">
import type { ConversationState } from "~/composables/useSarjyRoom";

const props = defineProps<{
  level: number;
  state: ConversationState;
  muted?: boolean;
}>();
const { t } = useI18n();

const SEGMENTS = 10;
const active = computed(() => Math.round(props.level * SEGMENTS));

// Real amplitude drives the bars only while it's actually your turn and
// you're not muted — during thinking/speaking your mic level is
// near-silent anyway (true signal), so cells rest at a fixed low mark
// instead of guttering at whatever noise-floor number the analyser
// happens to report. Forced to rest while muted too, regardless of what
// the underlying track reports — the meter must never look "live" when
// nothing is actually being sent, or muting stops feeling trustworthy.
const restingCells = 2;
const displayActive = computed(() =>
  props.state === "listening" && !props.muted ? active.value : restingCells,
);

const statusLabel = computed(() => {
  if (props.state === "speaking") return t("agentSpeaking");
  if (props.state === "thinking") return t("thinking");
  if (props.state === "preparing") return t("preparing");
  // Only overrides "listening" — mid-turn, what Sarjy is doing is more
  // useful than a mute reminder you're already seeing on the button.
  if (props.muted) return t("muted");
  return t("listening");
});
</script>

<template>
  <div class="voice-status">
    <span class="voice-status__label" :class="`voice-status__label--${state}`">
      {{ statusLabel }}
    </span>
    <div
      class="voice-meter"
      :class="`voice-meter--${state}`"
      role="img"
      :aria-label="`${t('micLevel')}: ${statusLabel}`"
    >
      <span
        v-for="i in SEGMENTS"
        :key="i"
        class="voice-meter__cell"
        :class="{ 'voice-meter__cell--active': i <= displayActive }"
        :style="{
          transitionDelay: state !== 'listening' ? `${i * 40}ms` : '0ms',
        }"
      />
    </div>
  </div>
</template>

<style scoped>
.voice-status {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
}

.voice-status__label {
  font-size: 0.7rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-muted);
  transition: font-weight 200ms ease;
}

/* Weight carries the emphasis, not color — the brass accent as plain
   text (no fill shape behind it) measures ~3.2:1 against this
   background, under the 4.5:1 WCAG floor. Accent stays reserved for the
   bars themselves (a filled shape), never for running text. */
.voice-status__label--thinking,
.voice-status__label--speaking {
  font-weight: 700;
  color: rgb(var(--v-theme-on-surface));
}

.voice-meter {
  display: inline-flex;
  gap: 3px;
  align-items: flex-end;
  height: 1.5rem;
}

.voice-meter__cell {
  width: 5px;
  height: 100%;
  background-color: rgba(43, 36, 28, 0.12);
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
  transition:
    background-color 80ms linear,
    transform 80ms linear;
  transform: scaleY(0.55);
  transform-origin: bottom;
}

.voice-meter__cell--active {
  background-color: rgb(var(--v-theme-primary));
  transform: scaleY(1);
}

/* Idle breath: only while genuinely listening with nothing above the
   floor for a beat — tells you the mic is live, not frozen, without
   inventing amplitude that was never measured. One authored moment,
   reused (not duplicated) across every rest state below.
   :not(--active) matters: real speech reaching these two cells would
   otherwise fight the breathe keyframe's opacity cycle against the
   active-state fill, producing a visible flicker right at the moment
   your voice actually registers. */
.voice-meter--listening
  .voice-meter__cell:nth-child(5):not(.voice-meter__cell--active),
.voice-meter--listening
  .voice-meter__cell:nth-child(6):not(.voice-meter__cell--active),
.voice-meter--preparing
  .voice-meter__cell:nth-child(5):not(.voice-meter__cell--active),
.voice-meter--preparing
  .voice-meter__cell:nth-child(6):not(.voice-meter__cell--active) {
  animation: voice-meter-breathe 2.6s ease-in-out infinite;
}

.voice-meter--thinking .voice-meter__cell--active,
.voice-meter--speaking .voice-meter__cell--active {
  background-color: rgba(176, 122, 34, 0.55);
  animation: voice-meter-breathe 1.4s ease-in-out infinite;
}

@keyframes voice-meter-breathe {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .voice-meter__cell {
    animation: none !important;
  }
}
</style>
