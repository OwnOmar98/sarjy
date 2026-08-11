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
  <div class="lattice-cells">
    <p v-if="!stages.length" class="text-body-2 text-medium-emphasis pa-4">
      {{ t("waitingForTurn") }}
    </p>
    <div v-else class="lattice-cells__grid">
      <div v-for="s in stages" :key="s.stage" class="lattice-cells__cell">
        <span class="lattice-cells__stage">{{ s.stage }}</span>
        <span class="lattice-cells__ms">{{ s.ms }}ms</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  opacity: 0.7;
}

.lattice-cells__ms {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 1.1rem;
  font-weight: 600;
}
</style>
