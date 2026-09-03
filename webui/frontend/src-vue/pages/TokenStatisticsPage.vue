<template>
  <div class="page-stack">
    <PageHeader
      index="03"
      title="Token 统计"
      description="读取同一份本地 Token 账本，展示 Prompt/Completion 分解、调用趋势和模型、供应商、用途、群聊分布。供应商额度与本地消耗分开呈现。"
    />

    <div class="segmented-control" role="tablist" aria-label="统计时间范围">
      <button
        v-for="item in WINDOWS"
        :key="item.key"
        type="button"
        role="tab"
        :aria-selected="activeWindow === item.key"
        @click="setWindow(item.key)"
      >
        {{ item.label }}
      </button>
    </div>

    <QueryBoundary :pending="isPending" :error="error">
      <template v-if="data">
        <section class="metric-rack" aria-label="Token 总览">
          <article>
            <span>Prompt Token</span>
            <strong>{{ formatInteger(total.prompt_tokens) }}</strong>
            <small>输入与上下文</small>
          </article>
          <article>
            <span>Completion Token</span>
            <strong>{{ formatInteger(total.completion_tokens) }}</strong>
            <small>模型可见输出</small>
          </article>
          <article>
            <span>Total Token</span>
            <strong>{{ formatInteger(total.total_tokens) }}</strong>
            <small>{{ formatInteger(total.call_count) }} 次模型调用</small>
          </article>
          <article>
            <span>平均每次调用</span>
            <strong>{{ formatInteger(Math.round(average)) }}</strong>
            <small>{{ data.series?.[0]?.label || data.series?.[0]?.bucket || '—' }} → {{ data.series?.at(-1)?.label || data.series?.at(-1)?.bucket || '—' }}</small>
          </article>
        </section>

        <Panel eyebrow="TOKEN LEDGER / SERIES" title="Prompt 与 Completion 趋势">
          <div v-if="data.series?.length" class="token-chart" role="img" :aria-label="`Token 趋势，共 ${data.series.length} 个时间桶`" tabindex="0">
            <div
              v-for="(row, index) in data.series"
              :key="`${row.bucket || row.label || index}:${index}`"
              class="token-bar"
              tabindex="0"
              :aria-label="`${row.label || row.bucket || index + 1}，Prompt ${row.prompt_tokens || 0}，Completion ${row.completion_tokens || 0}，总计 ${row.total_tokens || 0}`"
              :title="`${row.label || row.bucket || index + 1}\nPrompt ${formatInteger(row.prompt_tokens)}\nCompletion ${formatInteger(row.completion_tokens)}\n总计 ${formatInteger(row.total_tokens)}`"
            >
              <i class="token-bar-prompt" :style="{ height: barHeight(row.prompt_tokens) }" />
              <i class="token-bar-completion" :style="{ height: barHeight(row.completion_tokens) }" />
              <span>{{ data.series.length <= 24 || index % Math.ceil(data.series.length / 12) === 0 ? (row.label || row.bucket || String(index + 1)).slice(-5) : '' }}</span>
            </div>
            <div class="token-chart-legend">
              <span><i class="legend-prompt" />Prompt</span>
              <span><i class="legend-completion" />Completion</span>
            </div>
          </div>
          <EmptyState v-else code="token_series_empty">
            当前范围没有模型调用记录。
          </EmptyState>
        </Panel>

        <div class="overview-grid">
          <Panel eyebrow="DISTRIBUTION" title="消耗分布">
            <template #actions>
              <div class="inline-controls">
                <select v-model="distribution" aria-label="分布维度">
                  <option value="model">按模型</option>
                  <option value="provider">按供应商</option>
                  <option value="purpose">按用途</option>
                  <option value="group">按群聊</option>
                </select>
                <select v-model="sortKey" aria-label="排序字段">
                  <option value="total_tokens">总 Token</option>
                  <option value="call_count">调用次数</option>
                  <option value="prompt_tokens">Prompt</option>
                  <option value="completion_tokens">Completion</option>
                </select>
              </div>
            </template>

            <div v-if="rows.length" class="trace-table-wrap">
              <table class="forensic-table">
                <thead>
                  <tr>
                    <th>项目</th>
                    <th>调用</th>
                    <th>Prompt</th>
                    <th>Completion</th>
                    <th>总计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, index) in rows" :key="`${rowLabel(row)}:${index}`">
                    <td>{{ rowLabel(row) }}</td>
                    <td>{{ formatInteger(row.call_count) }}</td>
                    <td>{{ formatInteger(row.prompt_tokens) }}</td>
                    <td>{{ formatInteger(row.completion_tokens) }}</td>
                    <td><strong>{{ formatInteger(row.total_tokens) }}</strong></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState v-else code="token_distribution_empty">
              当前维度没有可展示记录。
            </EmptyState>
          </Panel>

          <Panel eyebrow="BILLING / QUOTA" title="费用与供应商额度">
            <p class="billing-notice">
              {{ data.billing?.cost_configured ? `${data.billing.currency} ${data.billing.request_cost.toFixed(4)}` : '未配置价格，无法计算费用' }}
            </p>
            <p class="muted-copy">{{ data.billing?.note }}</p>
            <div class="quota-list">
              <div v-for="row in data.provider_usage ?? []" :key="row.provider">
                <span>{{ row.label }}</span>
                <strong>{{ formatInteger(row.total_tokens) }}</strong>
                <small>{{ row.unlimited ? '未设置月度额度' : `${(row.usage_ratio * 100).toFixed(1)}% / ${formatInteger(row.monthly_limit)}` }}</small>
              </div>
            </div>
          </Panel>
        </div>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { storeToRefs } from "pinia";
import { useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import type { TokenUsageRow } from "@/api/types";
import { formatInteger } from "@/lib/format";
import EmptyState from "@vue-app/components/EmptyState.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import { useBotStore } from "@vue-app/stores/bot";

type WindowKey = "24h" | "7d" | "30d" | "all";
type SortKey = "total_tokens" | "call_count" | "prompt_tokens" | "completion_tokens";
const WINDOWS: Array<{ key: WindowKey; label: string }> = [
  { key: "24h", label: "最近 24 小时" },
  { key: "7d", label: "最近 7 天" },
  { key: "30d", label: "最近 30 天" },
  { key: "all", label: "累计" },
];

const route = useRoute();
const router = useRouter();
const botStore = useBotStore();
const { selectedBotId } = storeToRefs(botStore);

const activeWindow = computed<WindowKey>(() => {
  const param = String(route.params.window ?? "24h");
  return (WINDOWS.some((item) => item.key === param) ? param : "24h") as WindowKey;
});

function setWindow(key: WindowKey) {
  void router.push(`/runtime/tokens/${key}`);
}

const distribution = ref<"model" | "provider" | "purpose" | "group">("model");
const sortKey = ref<SortKey>("total_tokens");

const { data, isPending, error } = useQuery({
  queryKey: ["token-metrics", activeWindow, selectedBotId],
  queryFn: ({ signal }) => resources.metrics(activeWindow.value, selectedBotId.value, signal),
});

const total = computed(() => data.value?.total ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, call_count: 0 });
const average = computed(() => (total.value.call_count ? total.value.total_tokens / total.value.call_count : 0));

const rows = computed(() => {
  if (!data.value) return [];
  const selected =
    distribution.value === "model"
      ? data.value.by_model
      : distribution.value === "provider"
        ? data.value.provider_usage
        : distribution.value === "purpose"
          ? data.value.by_purpose
          : data.value.by_group;
  return [...(selected ?? [])].sort((left, right) => Number(right[sortKey.value] || 0) - Number(left[sortKey.value] || 0));
});

const seriesMax = computed(() => Math.max(1, ...(data.value?.series ?? []).map((row) => Number(row.total_tokens || 0))));
function barHeight(val: number | undefined): string {
  return `${Math.max(0, (Number(val || 0) / seriesMax.value) * 100)}%`;
}

function rowLabel(row: TokenUsageRow): string {
  return row.model || row.provider || row.purpose_label || row.purpose || row.group_label || row.group_id || row.label || row.bucket || "未标注";
}
</script>
