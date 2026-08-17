<script setup lang="ts">
const route = useRoute();
</script>

<template>
  <SarjyApp :initial-session-id="route.params.id as string" />
</template>
