<template>
  <div class="empty-state" role="status">
    <span class="empty-mark" aria-hidden="true">∅</span>
    <p><slot /></p>
    <code v-if="code">{{ code }}</code>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  code?: string;
}>();
</script>

<style scoped>
.empty-state {
  display: grid;
  gap: var(--space-2);
  place-items: center;
  padding: var(--space-6);
  color: var(--color-ink-muted);
}
.empty-mark {
  font-size: var(--text-2xl);
}
</style>
