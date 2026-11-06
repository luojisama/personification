<template>
  <div class="query-boundary">
    <div v-if="pending" class="query-loading" role="status">
      <slot name="pending">
        <span class="query-loading-indicator">
          <span class="query-loading-dots" aria-hidden="true"><i /><i /><i /></span>
          加载中…
        </span>
      </slot>
    </div>
    <div v-else-if="error" class="query-error" role="alert"><slot name="error" :error="error">{{ errorMessage }}</slot></div>
    <div v-else-if="empty" class="query-empty"><slot name="empty">{{ emptyText }}</slot></div>
    <slot v-else />
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(defineProps<{ pending: boolean; error: unknown; empty?: boolean; emptyText?: string }>(), {
  empty: false,
  emptyText: "暂无数据",
});
const errorMessage = computed(() => props.error instanceof Error ? props.error.message : "请求失败");
</script>

<style scoped>
.query-loading-indicator {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.query-loading-dots {
  display: inline-flex;
  gap: 0.2rem;
  color: var(--color-signal);
}

.query-loading-dots i {
  width: 0.3rem;
  height: 0.3rem;
  border-radius: 50%;
  background: currentColor;
}

@media (prefers-reduced-motion: no-preference) {
  .query-loading-dots i { animation: query-dot 1.1s ease-in-out 4; }
  .query-loading-dots i:nth-child(2) { animation-delay: 100ms; }
  .query-loading-dots i:nth-child(3) { animation-delay: 200ms; }
}

@keyframes query-dot {
  50% { opacity: 0.45; transform: translateY(-0.2rem); }
}
</style>
