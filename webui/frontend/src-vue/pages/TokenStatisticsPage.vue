<template>
  <div class="page-stack">
    <PageHeader
      index="03"
      title="Token 统计"
      description="按真实调用账本查看输入、输出、缓存用量与多币种费用。未知缓存和未计价调用保持未知，不折算为 0；订阅额度独立展示。"
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
    <Panel eyebrow="FILTERS" title="统计范围"
      ><div class="filter-grid">
        <label v-for="filter in filterDefinitions" :key="filter.key"
          >{{ filter.label
          }}<select v-model="filters[filter.key]">
            <option value="">全部</option>
            <option
              v-for="option in filter.options"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select></label
        >
      </div>
      <p class="muted-copy">
        筛选由服务端应用；不同 Bot、群、路由或用途的数据不会在浏览器端拼接。
      </p></Panel
    >
    <QueryBoundary
      :pending="usageQuery.isPending.value"
      :error="usageQuery.error.value"
    >
      <template v-if="usage">
        <section class="metric-rack" aria-label="Token 总览">
          <article>
            <span>输入 Token</span
            ><strong>{{ integer(usage.input_tokens) }}</strong
            ><small>{{ integer(usage.call_count) }} 次调用</small>
          </article>
          <article>
            <span>输出 Token</span
            ><strong>{{ integer(usage.output_tokens) }}</strong
            ><small>模型输出</small>
          </article>
          <article>
            <span>总 Token</span
            ><strong>{{ integer(usage.total_tokens) }}</strong
            ><small>输入与输出合计</small>
          </article>
          <article>
            <span>缓存读取</span
            ><strong>{{
              knownToken(usage.cache_read_tokens, usage.cache_read_known_calls)
            }}</strong
            ><small>{{ coverageLabel(usage.cache_read_known_calls) }}</small>
          </article>
          <article>
            <span>缓存创建</span
            ><strong>{{
              knownToken(
                usage.cache_creation_tokens,
                usage.cache_creation_known_calls,
              )
            }}</strong
            ><small>{{
              coverageLabel(usage.cache_creation_known_calls)
            }}</small>
          </article>
        </section>
        <Panel eyebrow="TOKEN LEDGER / SERIES" title="输入与输出趋势"
          ><TokenUsageChart
            v-if="usage.series.length"
            :data="usage.series"
          /><EmptyState v-else code="token_series_empty"
            >当前范围没有模型调用记录。</EmptyState
          ></Panel
        >
        <div class="overview-grid">
          <Panel eyebrow="CACHE OBSERVABILITY" title="缓存遥测覆盖"
            ><dl class="detail-list">
              <div>
                <dt>缓存口径完整覆盖</dt>
                <dd>{{ percentOrUnknown(usage.cache_usage_coverage) }}</dd>
              </div>
              <div>
                <dt>完整调用</dt>
                <dd>
                  {{ integer(usage.cache_usage_complete_calls) }} /
                  {{ integer(usage.call_count) }}
                </dd>
              </div>
              <div>
                <dt>缓存读取 / 输入</dt>
                <dd>{{ percentOrUnknown(usage.cache_read_input_ratio) }}</dd>
              </div>
              <div>
                <dt>旧版未归因调用</dt>
                <dd>{{ integer(usage.legacy_unattributed) }}</dd>
              </div>
            </dl>
            <p class="muted-copy">
              按各 Provider 的缓存 usage
              合同完整返回且数量口径无矛盾，才算完整调用；仅部分缓存字段已知仍不进入覆盖率或比例。未知值不显示为
              0。
            </p></Panel
          ><Panel eyebrow="LOCAL LEDGER / COST" title="按自定义价格估算"
            ><div v-if="usage.costs.length" class="cost-list">
              <div v-for="cost in usage.costs" :key="cost.currency">
                <span>{{ cost.currency }}</span
                ><strong>{{ cost.cost_decimal }}</strong
                ><small
                  >{{ integer(cost.priced_call_count) }} 次已计价调用</small
                >
              </div>
            </div>
            <EmptyState v-else code="token_cost_unpriced"
              >当前范围没有可确认的已计价费用。</EmptyState
            >
            <p class="billing-notice">
              未计价 {{ integer(usage.unpriced_call_count) }} 次 ·
              用量或价格信息不完整
              {{ integer(usage.incomplete_priced_call_count) }} 次
            </p>
            <p class="muted-copy">
              不同币种分别显示，不进行隐式汇率换算。
            </p></Panel
          >
        </div>
        <Panel
          eyebrow="LEGACY LEDGER / GLOBAL"
          title="旧账本（全局、不受当前 Bot 筛选）"
        >
          <QueryBoundary
            :pending="legacyQuery.isPending.value"
            :error="legacyQuery.error.value"
          >
            <div v-if="legacyQuery.data.value" class="detail-list">
              <div>
                <span>Prompt Token</span
                ><strong>{{
                  integer(legacyQuery.data.value.total.prompt_tokens)
                }}</strong>
              </div>
              <div>
                <span>Completion Token</span
                ><strong>{{
                  integer(legacyQuery.data.value.total.completion_tokens)
                }}</strong>
              </div>
              <div>
                <span>总 Token</span
                ><strong>{{
                  integer(legacyQuery.data.value.total.total_tokens)
                }}</strong>
              </div>
              <div>
                <span>调用次数</span
                ><strong>{{
                  integer(legacyQuery.data.value.total.call_count)
                }}</strong>
              </div>
            </div>
          </QueryBoundary>
        </Panel>
        <Panel eyebrow="RECENT EVENTS" title="最近调用与重算预览"
          ><template #actions
            ><div class="event-actions">
              <select v-model="previewVersion">
                <option value="">选择价格版本</option>
                <option
                  v-for="price in prices"
                  :key="price.version_id"
                  :value="price.version_id"
                >
                  {{ price.route_id }} / {{ price.model || "默认" }} /
                  {{ price.currency }} / {{ time(price.effective_from) }}
                </option></select
              ><button
                class="button button-secondary"
                type="button"
                :disabled="
                  !selectedEventIds.length ||
                  !previewVersion ||
                  previewMutation.isPending.value
                "
                @click="previewMutation.mutate()"
              >
                {{
                  previewMutation.isPending.value
                    ? "计算中…"
                    : `预览重算（${selectedEventIds.length}）`
                }}
              </button>
            </div></template
          >
          <div v-if="usage.recent_events.length" class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th><span class="sr-only">选择</span></th>
                  <th>时间</th>
                  <th>路由 / 模型</th>
                  <th>用途 / Scope</th>
                  <th>输入 / 输出</th>
                  <th>缓存读 / 创建</th>
                  <th>账本费用</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="event in usage.recent_events" :key="event.event_id">
                  <td>
                    <input
                      v-model="selectedEventIds"
                      type="checkbox"
                      :value="event.event_id"
                      :aria-label="`选择事件 ${event.event_id}`"
                    />
                  </td>
                  <td>{{ time(event.observed_at) }}</td>
                  <td>
                    {{ event.route_id || event.provider || "未归因"
                    }}<small>{{ event.model || "未知模型" }}</small>
                  </td>
                  <td>
                    {{ event.purpose || "未知用途"
                    }}<small>{{ scopeLabel(event) }}</small>
                  </td>
                  <td>
                    {{ integer(event.input_tokens) }} /
                    {{ integer(event.output_tokens) }}
                  </td>
                  <td>
                    {{ nullableInteger(event.cache_read_tokens) }} /
                    {{ nullableInteger(event.cache_creation_tokens) }}
                  </td>
                  <td>
                    {{
                      event.cost_decimal === null || !event.currency
                        ? "未计价"
                        : `${event.currency} ${event.cost_decimal}`
                    }}<small v-if="!event.pricing_complete"
                      >价格信息不完整</small
                    >
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else code="token_events_empty"
            >当前范围没有最近调用事件。</EmptyState
          >
          <div
            v-if="previewMutation.data.value"
            class="preview-result"
            role="status"
          >
            <strong>预览结果（不会修改历史）</strong
            ><span
              v-for="cost in previewMutation.data.value.costs"
              :key="cost.currency"
              >{{ cost.currency }} {{ cost.cost_decimal }}（{{
                cost.priced_call_count
              }}
              次）</span
            ><span
              >未计价
              {{ previewMutation.data.value.unpriced_call_count }} 次；不完整
              {{ previewMutation.data.value.incomplete_priced_call_count }}
              次</span
            >
          </div>
          <p v-if="previewMutation.error.value" class="form-error" role="alert">
            重算预览失败；历史账本没有修改。
          </p></Panel
        >
        <Panel eyebrow="IMMUTABLE PRICING" title="价格版本管理"
          ><TokenPriceEditor
        /></Panel>
        <Panel
          v-if="quotaData?.items?.length"
          eyebrow="SUBSCRIPTION WINDOWS"
          title="订阅窗口额度"
          ><template #actions
            ><button
              type="button"
              class="ghost-button"
              :disabled="quotaQuery.isFetching.value"
              @click="forceRefreshQuota"
            >
              {{ quotaQuery.isFetching.value ? "查询中…" : "强制刷新" }}
            </button></template
          >
          <p class="muted-copy">
            额度来自订阅代理只读接口，与本地账本及费用估算相互独立。
          </p>
          <div class="quota-list">
            <article
              v-for="snapshot in quotaData.items"
              :key="snapshot.route_fingerprint"
            >
              <span>{{ snapshot.route_name }}</span
              ><strong>{{ quotaStatusLabel(snapshot.status) }}</strong
              ><small>{{ snapshot.diagnostic_code }}</small
              ><template
                v-if="
                  snapshot.status === 'available' || snapshot.status === 'stale'
                "
                ><div
                  v-for="window in snapshot.windows"
                  :key="`${snapshot.route_fingerprint}:${window.limit_window_seconds}`"
                >
                  <label
                    :for="`quota-${snapshot.route_fingerprint}-${window.limit_window_seconds}`"
                    >{{
                      quotaWindowLabel(
                        window.window_type,
                        window.limit_window_seconds,
                      )
                    }}：已用 {{ window.used_percent.toFixed(1) }}%，剩余
                    {{ window.remaining_percent.toFixed(1) }}%</label
                  ><progress
                    :id="`quota-${snapshot.route_fingerprint}-${window.limit_window_seconds}`"
                    :value="window.used_percent"
                    max="100"
                  /><small>重置时间：{{ resetTime(window.reset_at) }}</small>
                </div></template
              >
            </article>
          </div></Panel
        >
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { storeToRefs } from "pinia";
import {
  tokenBillingApi,
  type TokenUsageDimensions,
  type TokenUsageEvent,
  type UsageDimensionValue,
  type UsageWindow,
} from "@/api/tokenBilling";
import { resources } from "@/api/resources";
import type {
  SubscriptionQuotaStatus,
  SubscriptionQuotaWindowType,
} from "@/api/types";
import EmptyState from "@vue-app/components/EmptyState.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import TokenUsageChart from "@vue-app/components/TokenUsageChart.vue";
import TokenPriceEditor from "@vue-app/components/TokenPriceEditor.vue";
import { useBotStore } from "@vue-app/stores/bot";
const WINDOWS: Array<{ key: UsageWindow; label: string }> = [
  { key: "24h", label: "最近 24 小时" },
  { key: "7d", label: "最近 7 天" },
  { key: "30d", label: "最近 30 天" },
  { key: "all", label: "累计" },
];
type FilterKey =
  "bot_id" | "group_id" | "provider" | "route_id" | "model" | "purpose";
const route = useRoute(),
  router = useRouter();
const { selectedBotId } = storeToRefs(useBotStore());
const activeWindow = computed<UsageWindow>(() =>
  WINDOWS.some((item) => item.key === route.params.window)
    ? (route.params.window as UsageWindow)
    : "24h",
);
const filters = reactive<Record<FilterKey, string>>({
  bot_id: selectedBotId.value,
  group_id: "",
  provider: "",
  route_id: "",
  model: "",
  purpose: "",
});
watch(selectedBotId, (value) => {
  filters.bot_id = value;
});
const usageQuery = useQuery({
  queryKey: computed(() => [
    "token-usage",
    activeWindow.value,
    ...Object.values(filters),
  ]),
  queryFn: ({ signal }) =>
    tokenBillingApi.usage({ window: activeWindow.value, ...filters }, signal),
});
const usage = computed(() => usageQuery.data.value);
const legacyQuery = useQuery({
  queryKey: computed(() => ["legacy-token-metrics-global", activeWindow.value]),
  queryFn: ({ signal }) => resources.metrics(activeWindow.value, "", signal),
});
const pricesQuery = useQuery({
  queryKey: ["token-prices"],
  queryFn: ({ signal }) => tokenBillingApi.prices(signal),
});
const prices = computed(() => pricesQuery.data.value?.items ?? []);
const dimensionMap: Array<{
  key: FilterKey;
  dimension: keyof TokenUsageDimensions;
  label: string;
}> = [
  { key: "bot_id", dimension: "bot_ids", label: "Bot" },
  { key: "group_id", dimension: "group_ids", label: "群聊" },
  { key: "provider", dimension: "providers", label: "供应商" },
  { key: "route_id", dimension: "route_ids", label: "路由" },
  { key: "model", dimension: "models", label: "模型" },
  { key: "purpose", dimension: "purposes", label: "用途" },
];
const filterDefinitions = computed(() =>
  dimensionMap.map((item) => ({
    key: item.key,
    label: item.label,
    options: options(usage.value?.dimensions?.[item.dimension] ?? []),
  })),
);
function options(values: UsageDimensionValue[]) {
  return values.map((value) =>
    typeof value === "string"
      ? { value, label: value }
      : { value: value.value, label: value.label || value.value },
  );
}
function setWindow(key: UsageWindow) {
  void router.push(`/runtime/tokens/${key}`);
}
const selectedEventIds = ref<string[]>([]),
  previewVersion = ref("");
watch(
  () => usage.value?.recent_events,
  () => {
    selectedEventIds.value = [];
  },
);
const previewMutation = useMutation({
  mutationFn: () =>
    tokenBillingApi.repricePreview(
      previewVersion.value,
      selectedEventIds.value,
    ),
});
const quotaForce = ref(false),
  quotaQuery = useQuery({
    queryKey: ["subscription-quotas"],
    queryFn: ({ signal }) =>
      resources.subscriptionQuotas(quotaForce.value, signal),
  }),
  quotaData = quotaQuery.data;
async function forceRefreshQuota() {
  quotaForce.value = true;
  try {
    await quotaQuery.refetch();
  } finally {
    quotaForce.value = false;
  }
}
function integer(value: unknown) {
  return Intl.NumberFormat("zh-CN").format(Number(value ?? 0));
}
function nullableInteger(value: number | null | undefined) {
  return value === null || value === undefined ? "未知" : integer(value);
}
function knownToken(value: number | null, knownCalls: number) {
  return knownCalls === 0 || value === null ? "未知" : integer(value);
}
function coverageLabel(knownCalls: number) {
  return `${integer(knownCalls)} / ${integer(usage.value?.call_count)} 次已知`;
}
function percentOrUnknown(value: number | null) {
  return value === null ? "未知" : `${(value * 100).toFixed(1)}%`;
}
function time(value: number | undefined) {
  return value ? new Date(value * 1000).toLocaleString("zh-CN") : "未知";
}
function scopeLabel(event: TokenUsageEvent) {
  return (
    [
      event.bot_id && `Bot ${event.bot_id}`,
      event.group_id && `群 ${event.group_id}`,
    ]
      .filter(Boolean)
      .join(" · ") || "未归因"
  );
}
function quotaStatusLabel(status: SubscriptionQuotaStatus) {
  return {
    available: "可用",
    not_configured: "未配置",
    auth_failed: "认证失败",
    upstream_failed: "上游失败",
    unsupported: "结构不支持",
    stale: "缓存已过期",
  }[status];
}
function quotaWindowLabel(type: SubscriptionQuotaWindowType, seconds: number) {
  return type === "five_hour"
    ? "五小时窗口"
    : type === "weekly"
      ? "七天窗口"
      : `其它窗口（${seconds} 秒）`;
}
function resetTime(value: number | null) {
  return value ? time(value) : "未知";
}
</script>

<style scoped>
.filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: 0.7rem;
}
.filter-grid label {
  display: grid;
  gap: 0.3rem;
  font-size: 0.82rem;
}
.filter-grid select,
.event-actions select {
  min-height: 2.5rem;
  padding: 0.5rem;
  border: 1px solid var(--color-line);
  border-radius: 0.5rem;
  background: var(--color-surface-raised);
  color: inherit;
}
.detail-list,
.cost-list {
  display: grid;
  gap: 0.65rem;
}
.detail-list div,
.cost-list div {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--color-line);
  padding-bottom: 0.5rem;
}
.detail-list dt,
.cost-list span {
  color: var(--color-ink-muted);
}
.detail-list dd {
  margin: 0;
  font-weight: 700;
}
.cost-list div {
  display: grid;
  grid-template-columns: 1fr auto;
}
.cost-list small {
  grid-column: 1/-1;
}
.billing-notice {
  font-weight: 700;
}
.event-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.forensic-table small {
  display: block;
  color: var(--color-ink-muted);
}
.forensic-table input[type="checkbox"] {
  inline-size: 1rem;
  block-size: 1rem;
}
.preview-result {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  padding: 0.8rem;
  border: 1px solid var(--color-line);
  border-radius: 0.6rem;
  margin-top: 0.8rem;
}
.form-error {
  color: var(--color-danger);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
@media (max-width: 720px) {
  .event-actions {
    align-items: stretch;
  }
  .event-actions > * {
    width: 100%;
  }
}
</style>
