<!--
  Latency waterfall stub (docs/PRD.md §3-4). TODO(day 2): wire to the
  agent's "latency" room-data topic, rolling window, AR/EN toggle.
  Lattice-cell styling per app.vue's direction-contract comment.
-->
<script setup lang="ts">
defineProps<{ stages: { stage: string; ms: number }[] }>();
const { t } = useI18n();
</script>

<template>
  <div>
    <!-- Distinct section label and empty-state wording from
      TranscriptPanel's — near-identical text on two adjacent panels
      reads as one frozen panel instead of two unrelated ones. -->
    <p class="lattice-cells__heading">{{ t("latencyHeading") }}</p>
    <div class="lattice-cells">
      <p v-if="!stages.length" class="text-body-2 lattice-cells__empty pa-4">
        {{ t("latencyEmpty") }}
      </p>
      <div v-else class="lattice-cells__grid">
        <div v-for="s in stages" :key="s.stage" class="lattice-cells__cell">
          <span class="lattice-cells__stage">{{ s.stage }}</span>
          <span class="lattice-cells__ms">{{ s.ms }}ms</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lattice-cells__heading {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-muted);
  margin-block-end: 0.5rem;
  text-align: start;
}

/* Vuetify's text-medium-emphasis utility (opacity: 0.6, Material's
   default) measures ~3.9:1 against this palette's sand — under the
   4.5:1 floor. Same --ink-muted token as everywhere else instead. */
.lattice-cells__empty {
  color: var(--ink-muted);
}

.lattice-cells {
  border: 1px solid rgba(43, 36, 28, 0.16);
}

.lattice-cells__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr));
}

.lattice-cells__cell {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.75rem 1rem;
  border: 1px solid rgba(43, 36, 28, 0.1);
}

.lattice-cells__stage {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-muted);
}

.lattice-cells__ms {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 1.1rem;
  font-weight: 600;
}
</style>
