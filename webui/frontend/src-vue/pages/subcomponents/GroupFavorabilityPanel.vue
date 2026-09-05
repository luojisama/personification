<template>
  <Panel :eyebrow="eyebrow" :title="text(favorability.level, '未分级')">
    <div class="favorability-score-block">
      <strong :class="['favorability-score', favorabilityDisplayClass(score)]">{{ formatNumber(score) }}</strong>
      <span>/ {{ formatNumber(scoreMin) }} ～ {{ formatNumber(scoreMax) }}</span>
      <progress :value="scoreProgress" max="100" aria-label="好感度分值进度" />
    </div>
    <KeyValueGrid>
      <KeyValueItem label="分数">
        <span :class="['favorability-numeric', favorabilityDisplayClass(score)]">
        {{ formatNumber(score) }} / {{ formatNumber(scoreMin) }} ～ {{ formatNumber(scoreMax) }}
        </span>
      </KeyValueItem>
      <KeyValueItem label="今日加分"><span class="favorability-numeric">{{ signed(favorability.today_positive ?? favorability.daily_positive_count) }}</span></KeyValueItem>
      <KeyValueItem label="今日扣分"><span class="favorability-numeric">{{ negative(favorability.daily_negative_count) }}</span></KeyValueItem>
      <KeyValueItem label="今日净变化"><span class="favorability-numeric">{{ signed(favorability.daily_net_count) }}</span></KeyValueItem>
      <KeyValueItem label="每日增长上限"><span class="favorability-numeric">{{ formatNumber(favorability.daily_growth_cap) }}</span></KeyValueItem>
      <KeyValueItem label="今日剩余额度"><span class="favorability-numeric">{{ formatNumber(favorability.remaining_today) }}</span></KeyValueItem>
      <KeyValueItem label="最近进展质量">{{ progressQualityLabel(favorability.last_progress_quality) }}</KeyValueItem>
      <KeyValueItem label="预计活跃至 70 分"><span class="favorability-numeric">{{ estimatedDays }}</span></KeyValueItem>
      <KeyValueItem label="群随机偏置"><span class="favorability-numeric">{{ signed(policy.random_reply_add) }}</span></KeyValueItem>
      <KeyValueItem label="群闲偏置"><span class="favorability-numeric">{{ signed(policy.group_idle_add) }}</span></KeyValueItem>
      <KeyValueItem label="数据状态">{{ dataState }}</KeyValueItem>
      <KeyValueItem label="接口可用">{{ triStateLabel(favorability.available) }}</KeyValueItem>
      <KeyValueItem label="功能启用">{{ triStateLabel(favorability.enabled) }}</KeyValueItem>
      <KeyValueItem label="档案已持久化">{{ triStateLabel(favorability.exists) }}</KeyValueItem>
      <KeyValueItem label="画像范围">{{ text(favorability.scope_used, text(favorability.scope, '未声明')) }}</KeyValueItem>
      <KeyValueItem label="回退使用">{{ triStateLabel(favorability.fallback_used) }}</KeyValueItem>
    </KeyValueGrid>
    <p v-if="dailyCapNote" class="muted-copy">{{ dailyCapNote }}</p>
  </Panel>
</template>

<script setup lang="ts">
import { computed } from "vue";
import KeyValueGrid from "@vue-app/components/KeyValueGrid.vue";
import KeyValueItem from "@vue-app/components/KeyValueItem.vue";
import Panel from "@vue-app/components/Panel.vue";
import { favorabilityDisplayClass, type JsonRecord, record, text } from "./managementHelpers";

const props = withDefaults(
  defineProps<{
    favorability?: JsonRecord;
    eyebrow?: string;
  }>(),
  {
    favorability: () => ({}),
    eyebrow: "GROUP / FAVORABILITY",
  },
);

const score = computed(() => finiteNumber(props.favorability.score, 0));
const scoreMin = computed(() => finiteNumber(props.favorability.score_min, -100));
const scoreMax = computed(() => finiteNumber(props.favorability.score_max, 100));
const policy = computed(() => record(props.favorability.behavior_policy));
const scoreProgress = computed(() => Math.max(0, Math.min(100, ((score.value - scoreMin.value) / Math.max(1, scoreMax.value - scoreMin.value)) * 100)));
const dataState = computed(() => {
  if (props.favorability.available === false) return "不可用";
  if (props.favorability.available !== true) return "状态未返回";
  if (props.favorability.enabled === false) return "功能已关闭";
  if (props.favorability.enabled !== true) return "启用状态未返回";
  if (props.favorability.exists === false) return "虚拟默认档案（未持久化）";
  if (props.favorability.exists !== true) return "持久化状态未返回";
  return "已持久化";
});
const dailyCapNote = computed(() => {
  const used = finiteNumber(props.favorability.today_positive ?? props.favorability.daily_positive_count, 0);
  const cap = finiteNumber(props.favorability.daily_growth_cap, 0);
  if (cap === 0) return "当前配置已关闭正向增长。";
  if (cap > 0 && used > cap + 1e-9) return "今日额度已用尽；累计可能来自旧配置或人工事件。";
  if (cap > 0 && used >= cap - 1e-9) return "今日额度已用尽。";
  return "";
});
const estimatedDays = computed(() => {
  if (finiteNumber(props.favorability.daily_growth_cap, 0) === 0 && score.value < 70) return "当前配置下不可增长";
  const value = finiteNumberOrNull(props.favorability.estimated_active_days_to_70);
  return value === null ? "—" : `${formatNumber(value)} 天`;
});

function finiteNumber(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function finiteNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatNumber(value: unknown): string {
  const parsed = finiteNumberOrNull(value);
  if (parsed === null) return "—";
  const normalized = Math.abs(parsed) < 1e-9 ? 0 : parsed;
  const absolute = Math.abs(normalized);
  const digits = Number.isInteger(absolute) ? String(absolute) : String(Number(absolute.toFixed(2)));
  return normalized < 0 ? `−${digits}` : digits;
}

function signed(value: unknown): string {
  const parsed = finiteNumberOrNull(value);
  if (parsed === null || Math.abs(parsed) < 1e-9) return parsed === null ? "—" : "0";
  return parsed > 0 ? `+${formatNumber(parsed)}` : formatNumber(parsed);
}

function negative(value: unknown): string {
  const parsed = finiteNumberOrNull(value);
  if (parsed === null || Math.abs(parsed) < 1e-9) return parsed === null ? "—" : "0";
  return `−${formatNumber(Math.abs(parsed))}`;
}

function triStateLabel(value: unknown): string {
  if (value === true) return "是";
  if (value === false) return "否";
  return "未返回";
}

function progressQualityLabel(value: unknown): string {
  const raw = text(value, "none");
  if (raw === "none") return "暂无";
  if (raw === "low") return "低质量";
  if (raw === "normal") return "常规";
  if (raw === "high") return "高质量";
  if (raw === "meaningful") return "有效进展";
  if (raw === "resonant") return "深度共鸣";
  if (raw === "milestone") return "重要里程碑";
  return `未知质量（${raw}）`;
}
</script>

<style scoped>
.favorability-numeric {
  text-align: end;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.favorability-score-block {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: baseline;
  gap: var(--space-1) var(--space-2);
  margin-bottom: var(--space-3);
}

.favorability-score {
  font-size: clamp(1.8rem, 4vw, 3rem);
  font-variant-numeric: tabular-nums;
}

.favorability-score-block progress {
  grid-column: 1 / -1;
  width: 100%;
}
</style>
