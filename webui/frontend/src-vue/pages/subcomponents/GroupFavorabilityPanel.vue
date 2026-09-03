<template>
  <Panel :eyebrow="eyebrow" :title="text(favorability.level, '未分级')">
    <dl class="compact-kv">
      <dt>分数</dt>
      <dd :class="favorabilityDisplayClass(score)">
        {{ text(favorability.score, "0") }} / {{ text(favorability.score_min, "-100") }}..{{ text(favorability.score_max, "100") }}
      </dd>
      <dt>今日加分</dt>
      <dd>{{ signed(favorability.daily_positive_count) }}</dd>
      <dt>今日扣分</dt>
      <dd>-{{ text(favorability.daily_negative_count, "0") }}</dd>
      <dt>今日净变化</dt>
      <dd>{{ signed(favorability.daily_net_count) }}</dd>
      <dt>群随机偏置</dt>
      <dd>{{ signed(policy.random_reply_add) }}</dd>
      <dt>群闲偏置</dt>
      <dd>{{ signed(policy.group_idle_add) }}</dd>
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

const score = computed(() => Number(props.favorability.score ?? 0));
const policy = computed(() => record(props.favorability.behavior_policy));

function signed(value: unknown): string {
  return `${Number(value ?? 0) >= 0 ? "+" : ""}${text(value, "0")}`;
}
</script>
