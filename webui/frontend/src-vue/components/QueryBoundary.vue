<template>
  <div class="query-boundary">
    <div v-if="pending" class="query-loading" role="status"><slot name="pending">加载中…</slot></div>
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
