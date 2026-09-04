<template>
  <Panel :eyebrow="eyebrow" :title="text(favorability.level, '未分级')">
    <dl class="compact-kv">
      <dt>分数</dt>
      <dd :class="['favorability-numeric', favorabilityDisplayClass(score)]">
        {{ formatNumber(score) }} / {{ formatNumber(scoreMin) }} ～ {{ formatNumber(scoreMax) }}
      </dd>
      <dt>今日加分</dt>
      <dd class="favorability-numeric">{{ signed(favorability.today_positive ?? favorability.daily_positive_count) }}</dd>
      <dt>今日扣分</dt>
      <dd class="favorability-numeric">{{ negative(favorability.daily_negative_count) }}</dd>
      <dt>今日净变化</dt>
      <dd class="favorability-numeric">{{ signed(favorability.daily_net_count) }}</dd>
      <dt>每日增长上限</dt>
      <dd class="favorability-numeric">{{ formatNumber(favorability.daily_growth_cap) }}</dd>
      <dt>今日剩余额度</dt>
      <dd class="favorability-numeric">{{ formatNumber(favorability.remaining_today) }}</dd>
      <dt>最近进展质量</dt>
      <dd>{{ progressQualityLabel(favorability.last_progress_quality) }}</dd>
      <dt>预计活跃至 70 分</dt>
      <dd class="favorability-numeric">{{ estimatedDays }}</dd>
      <dt>群随机偏置</dt>
      <dd class="favorability-numeric">{{ signed(policy.random_reply_add) }}</dd>
      <dt>群闲偏置</dt>
      <dd class="favorability-numeric">{{ signed(policy.group_idle_add) }}</dd>
    </dl>
  </Panel>
</template>

<script setup lang="ts">
import { computed } from "vue";
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
const estimatedDays = computed(() => {
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

function progressQualityLabel(value: unknown): string {
  const raw = text(value, "none");
  if (raw === "none") return "暂无";
  if (raw === "low") return "低质量";
  if (raw === "normal") return "常规";
  if (raw === "high") return "高质量";
  if (raw === "milestone") return "里程碑";
  return `未知质量（${raw}）`;
}
</script>

<style scoped>
.favorability-numeric {
  text-align: end;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
</style>
