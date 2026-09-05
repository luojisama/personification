<template>
  <dl class="coverage-grid" aria-label="源码覆盖率">
    <div><dt>文件</dt><dd>{{ numberAt("source_file_count") }}</dd></div>
    <div><dt>Chunks</dt><dd>{{ numberAt("source_chunk_count") }}</dd></div>
    <div><dt>源码字符</dt><dd>{{ numberAt("source_chars") }}</dd></div>
    <div><dt>分析策略</dt><dd>{{ textAt("analysis_strategy", "analysis_mode") }}</dd></div>
    <div><dt>输入状态</dt><dd>{{ inputState }}</dd></div>
  </dl>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ coverage?: unknown }>();
const row = computed<Record<string, unknown>>(() => (
  props.coverage && typeof props.coverage === "object" && !Array.isArray(props.coverage)
    ? props.coverage as Record<string, unknown>
    : {}
));

function numberAt(key: string): string {
  const raw = row.value[key];
  if (raw === null || raw === undefined || raw === "") return "—";
  const value = Number(raw);
  return Number.isFinite(value) ? value.toLocaleString("zh-CN") : "—";
}

function textAt(...keys: string[]): string {
  for (const key of keys) {
    const value = row.value[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "未声明";
}

const inputState = computed(() => {
  if (row.value.source_truncated === true || row.value.source_complete === false) return "已截断";
  if (row.value.full_input === true || row.value.source_complete === true) return "完整";
  return "未确认";
});
</script>

<style scoped>
.coverage-grid {
  display: grid;
  gap: .25rem;
  min-width: 12rem;
  margin: 0;
}
.coverage-grid > div { display: grid; grid-template-columns: 5rem minmax(0, 1fr); gap: .5rem; }
.coverage-grid dt, .coverage-grid dd { margin: 0; overflow-wrap: anywhere; }
</style>
