<template>
  <span :class="['state-badge', `state-${tone}`]" :title="raw || undefined">
    <span class="state-dot" aria-hidden="true" /><slot />
  </span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  tone?: "ok" | "warn" | "error" | "running" | "unknown";
  raw?: string;
}>(), { tone: "unknown", raw: "" });
</script>
