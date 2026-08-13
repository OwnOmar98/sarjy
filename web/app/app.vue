<script setup lang="ts">
const {
  connected,
  connecting,
  connectError,
  connect,
  disconnect,
  micLevel,
  transcript,
  latencyStages,
  latencyPercentiles,
  awaitingReply,
  conversationState,
  audioBlocked,
  resumeAudio,
} = useSarjyRoom();

const { locale, locales, t, setLocale } = useI18n();
const currentLocaleDir = computed(
  () => locales.value.find((l) => l.code === locale.value)?.dir ?? "ltr",
);

useHead({
  htmlAttrs: {
    lang: locale,
    dir: currentLocaleDir,
  },
});
</script>

<template>
  <!--
    THESIS: A voice interface built like a lattice — precise repeating
    geometry that filters what passes through it — refusing the
    rounded-bubble gradient chatbot default.
    OWN-WORLD: Warm sand (#F2EDE3) ground, walnut ink (#2B241C) text, one
    brass accent (#B07A22); a diamond-trellis + 8-point-star module
    (public/patterns/lattice.svg) structures surfaces and cuts the
    connect button's aperture — never a decorative border.
    STORY: A technical visitor reads precision instrumentation, not a
    toy assistant; presses the lattice's cut aperture to start talking;
    sees memory and latency render as disciplined lattice cells.
    FIRST VIEWPORT: A lattice-textured toolbar spans full width, title
    left, language toggle right; centered below, an octagonal "aperture"
    button starts the call; latency cells beneath share the same module
    grid.
    FORM: Mashrabiya Lattice, candidate 7 of 7, seed key b2d6d5e3.
    FINISH: unreviewed and undocumented is unfinished; this build ends
    with the finish review, the verdict, and DESIGN.md
  -->
  <v-app>
    <!--
      v-toolbar, not v-app-bar: v-app-bar registers with Vuetify's
      layout system, which reserves --v-layout-top only after client
      mount — a real SSR jump on first paint, no prop opts out of it.
      Fixed positioning isn't needed here, so v-toolbar (unregistered)
      has the same look with no jump.
    -->
    <v-toolbar
      flat
      density="comfortable"
      class="lattice-surface"
      style="--lattice-color: rgba(43, 36, 28, 0.05)"
    >
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

    <v-main
      class="lattice-surface d-flex"
      style="--lattice-color: rgba(43, 36, 28, 0.035); min-height: 85vh"
    >
      <v-container
        class="text-center d-flex flex-column justify-center"
        style="max-width: 32rem"
      >
        <!-- Signature interaction: octagonal "aperture" (the lattice's
          8-point construction), not a rounded pill — direction as form. -->
        <button
          v-if="!connected"
          class="aperture-button"
          type="button"
          :disabled="connecting"
          @click="connect"
        >
          {{ connecting ? t("connecting") : t("start") }}
        </button>
        <p v-if="connectError" class="connect-error" role="alert">
          {{
            connectError === "mic-denied" ? t("micDenied") : t("connectFailed")
          }}
        </p>
        <div v-if="connected" class="mb-6">
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
            class="mb-4"
          />
          <div>
            <v-btn variant="outlined" size="small" @click="disconnect">
              {{ t("stop") }}
            </v-btn>
          </div>
          <TranscriptPanel
            :entries="transcript"
            :awaiting-reply="awaitingReply"
            class="mt-6"
          />
        </div>

        <LatencyHud
          :stages="latencyStages"
          :percentiles="latencyPercentiles"
          class="mt-8"
        />
      </v-container>
    </v-main>
  </v-app>
</template>

<style scoped>
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
