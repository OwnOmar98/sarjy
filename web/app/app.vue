<script setup lang="ts">
const { locale, locales } = useI18n();
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
    <NuxtPage />
  </v-app>
</template>
