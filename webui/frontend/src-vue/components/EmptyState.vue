<template>
  <div class="empty-state" role="status">
    <span class="empty-mark" aria-hidden="true">∅</span>
    <p><slot /></p>
    <details v-if="code" class="empty-diagnostic">
      <summary>查看诊断标识</summary>
      <code>{{ code }}</code>
    </details>
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
.empty-diagnostic { color: var(--color-ink-muted); font-size: var(--text-xs); }
.empty-diagnostic summary { cursor: pointer; }
.empty-diagnostic code { display: inline-block; margin-top: var(--space-1); overflow-wrap: anywhere; }
</style>
