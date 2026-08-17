<!--
  Latency waterfall (docs/PRD.md §3-4): the current turn's per-stage
  breakdown, plus a running session p50/p95 per stage below it — the
  PRD's own target table is p50/p95, not a single turn's numbers, which
  can be a lucky or unlucky outlier on their own.
  Lattice-cell styling per app.vue's direction-contract comment.
-->
<script setup lang="ts">
import type {
  LatencyPercentile,
  LatencyStage,
} from "~/composables/useSarjyRoom";

defineProps<{ stages: LatencyStage[]; percentiles: LatencyPercentile[] }>();
const { t } = useI18n();
</script>

<template>
  <div>
    <!-- Distinct section label and empty-state wording from
      TranscriptPanel's — near-identical text on two adjacent panels
      reads as one frozen panel instead of two unrelated ones. -->
    <p class="lattice-cells__heading">{{ t("latencyHeading") }}</p>
    <div class="lattice-cells thin-scrollbar">
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

    <template v-if="percentiles.length">
      <p class="lattice-cells__heading mt-6">
        {{ t("latencyPercentilesHeading") }}
      </p>
      <div class="lattice-table-scroll thin-scrollbar">
        <table class="lattice-table">
          <thead>
            <tr>
              <th class="text-start">{{ t("latencyStageColumn") }}</th>
              <th class="text-end">p50</th>
              <th class="text-end">p95</th>
              <th class="text-end">n</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in percentiles" :key="p.stage">
              <td class="lattice-table__stage">{{ p.stage }}</td>
              <td class="text-end lattice-table__ms">{{ p.p50 }}ms</td>
              <td class="text-end lattice-table__ms">{{ p.p95 }}ms</td>
              <td class="text-end">{{ p.n }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
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

/* Bounded and internally scrollable, same treatment as every other
   content panel on this page (the transcripts) — the actual stage/
   percentile counts are small and fixed in practice, so this is
   consistency more than a real necessity, but it keeps the page's total
   height predictable rather than open-ended if that ever changes. */
.lattice-cells {
  max-height: 14rem;
  overflow-y: auto;
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

.lattice-table-scroll {
  max-height: 14rem;
  overflow-y: auto;
}

.lattice-table {
  width: 100%;
  border: 1px solid rgba(43, 36, 28, 0.16);
  border-collapse: collapse;
}

.lattice-table th,
.lattice-table td {
  padding: 0.5rem 0.75rem;
  border: 1px solid rgba(43, 36, 28, 0.1);
}

.lattice-table th {
  font-size: 0.7rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-muted);
}

.lattice-table__stage {
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--ink-muted);
}

.lattice-table__ms {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-weight: 600;
}
</style>
