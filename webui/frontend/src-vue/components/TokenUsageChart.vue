<template>
  <div class="usage-chart" role="img" :aria-label="ariaLabel" tabindex="0">
    <VisXYContainer
      :data="points"
      :height="280"
      :margin="{ top: 16, right: 18, bottom: 44, left: 64 }"
    >
      <VisLine :x="x" :y="inputY" color="var(--color-signal)" />
      <VisLine :x="x" :y="outputY" color="var(--color-positive)" />
      <VisCrosshair :x="x" :y="[inputY, outputY]" :template="tooltipTemplate" />
      <VisAxis type="x" :tick-format="xTick" />
      <VisAxis type="y" :tick-format="tokenTick" />
      <VisTooltip />
    </VisXYContainer>
    <div class="chart-legend" aria-hidden="true">
      <span><i class="input" />输入</span><span><i class="output" />输出</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import {
  VisAxis,
  VisCrosshair,
  VisLine,
  VisTooltip,
  VisXYContainer,
} from "@unovis/vue";
import type { TokenUsageSeriesPoint } from "@/api/tokenBilling";

const props = defineProps<{ data: TokenUsageSeriesPoint[] }>();
type Point = TokenUsageSeriesPoint & { index: number };
const points = computed<Point[]>(() =>
  props.data.map((item, index) => ({ ...item, index })),
);
const x = (d: Point) => d.index;
const inputY = (d: Point) => Number(d.input_tokens ?? 0);
const outputY = (d: Point) => Number(d.output_tokens ?? 0);
const labelAt = (index: number) =>
  points.value[
    Math.max(0, Math.min(points.value.length - 1, Math.round(index)))
  ]?.label ||
  points.value[
    Math.max(0, Math.min(points.value.length - 1, Math.round(index)))
  ]?.bucket ||
  "";
const xTick = (value: number) => labelAt(value);
const tokenTick = (value: number) =>
  Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
const tooltipTemplate = (d: Point) =>
  `<strong>${escapeHtml(d.label || d.bucket)}</strong><br>输入 ${format(d.input_tokens)}<br>输出 ${format(d.output_tokens)}<br>合计 ${format(d.total_tokens)}`;
const ariaLabel = computed(
  () =>
    `Token 趋势，共 ${points.value.length} 个时间桶；紫色为输入，绿色为输出。`,
);
function format(value: unknown): string {
  return Intl.NumberFormat("zh-CN").format(Number(value ?? 0));
}
function escapeHtml(value: string): string {
  return value.replace(
    /[&<>'"]/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[
        char
      ] || char,
  );
}
</script>

<style scoped>
.usage-chart {
  min-height: 20rem;
  outline: none;
}
.usage-chart:focus-visible {
  box-shadow: 0 0 0 2px var(--color-signal);
  border-radius: 0.5rem;
}
.chart-legend {
  display: flex;
  justify-content: center;
  gap: 1rem;
  font-size: 0.82rem;
  color: var(--color-ink-muted);
}
.chart-legend span {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.chart-legend i {
  width: 1.2rem;
  height: 0.18rem;
  border-radius: 1rem;
}
.chart-legend .input {
  background: var(--color-signal);
}
.chart-legend .output {
  background: var(--color-positive);
}
</style>
