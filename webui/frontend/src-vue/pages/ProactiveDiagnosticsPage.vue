<template>
  <div class="page-stack">
    <PageHeader
      index="主动诊断"
      title="主动行为诊断"
      description="按结构化结果查看发送、Agent 跳过、冷却和下一可用窗口。记录使用服务端游标读取，不再把接口对象拆成字段路径。"
    >
      <template v-if="section === 'recent'" #actions>
        <TextField
          :model-value="target"
          class="search-field"
          label="筛选目标 QQ / 群"
          hide-label
          type="search"
          placeholder="筛选目标 QQ / 群"
          @update:model-value="setFilter('target', $event)"
        />
      </template>
    </PageHeader>

    <div class="segmented-control" role="tablist" aria-label="主动行为类型">
      <button
        v-for="item in SCOPES"
        :key="item.value || 'all'"
        type="button"
        role="tab"
        :aria-selected="scope === item.value"
        @click="setFilter('scope', item.value)"
      >
        {{ item.label }}
      </button>
    </div>

    <Panel v-if="section === 'recent'" eyebrow="FILTER / OUTCOME" title="结果筛选">
      <SelectField
        :model-value="outcome"
        label="按结果筛选"
        :options="OUTCOME_OPTIONS"
        @update:model-value="setFilter('outcome', $event)"
      />
    </Panel>

    <QueryBoundary :pending="activeQuery.isPending.value" :error="activeQuery.error.value">
      <template v-if="section === 'overview' && statsQuery.data.value">
        <div class="metric-rack">
          <article>
            <span>触发总数</span>
            <strong>{{ statsQuery.data.value.total }}</strong>
            <small>最近 {{ statsQuery.data.value.since_hours }} 小时</small>
          </article>
          <article>
            <span>已发送</span>
            <strong>{{ statsQuery.data.value.sent }}</strong>
            <small>得到发送确认</small>
          </article>
          <article>
            <span>跳过</span>
            <strong>{{ statsQuery.data.value.skip }}</strong>
            <small>结构化 skip 原因</small>
          </article>
          <article>
            <span>发送率</span>
            <strong>{{ statsQuery.data.value.total ? `${Math.round((statsQuery.data.value.sent / statsQuery.data.value.total) * 100)}%` : "—" }}</strong>
            <small>不含未分类结果</small>
          </article>
        </div>

        <Panel eyebrow="STATISTICS / OUTCOME" title="结果分布">
          <div v-if="Object.keys(statsQuery.data.value.counts || {}).length" class="outcome-ledger">
            <div v-for="(value, key) in statsQuery.data.value.counts" :key="key">
              <StateBadge :tone="key === 'sent' ? 'ok' : 'warn'" :raw="String(key)">
                {{ outcomeLabel(String(key)) }}
              </StateBadge>
              <strong>{{ value }}</strong>
            </div>
          </div>
          <div v-else class="empty-state" data-code="proactive_stats_empty">
            最近 72 小时没有主动触发记录。
          </div>
        </Panel>
      </template>

      <template v-if="section === 'recent' && recentQuery.data.value">
        <Panel
          v-if="recentQuery.data.value.items.length"
          eyebrow="EVENTS / CURSOR"
          :title="`最近 ${recentQuery.data.value.items.length} 条触发记录`"
        >
          <div class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>类型</th>
                  <th>结果</th>
                  <th>对象</th>
                  <th>详情</th>
                  <th>下一触发</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in recentQuery.data.value.items" :key="item.id">
                  <td>{{ formatDateTime(item.ts) }}</td>
                  <td><code>{{ item.scope }}</code></td>
                  <td>
                    <StateBadge :tone="item.outcome === 'sent' ? 'ok' : 'warn'" :raw="item.outcome">
                      {{ outcomeLabel(item.outcome) }}
                    </StateBadge>
                  </td>
                  <td><code>{{ item.target || "—" }}</code></td>
                  <td class="wrap-cell">{{ detailSummary(item) }}</td>
                  <td>{{ formatDateTime(item.next_eligible_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>
        <div v-else class="empty-state" data-code="proactive_recent_empty">
          当前筛选条件下没有主动触发记录。
        </div>

        <div class="pagination">
          <button type="button" :disabled="!cursor" @click="setFilter('cursor', '')">
            回到最新
          </button>
          <span>{{ cursor ? `游标 ${cursor}` : "最新记录" }}</span>
          <button
            type="button"
            :disabled="!recentQuery.data.value.has_more || !recentQuery.data.value.next_cursor"
            @click="setFilter('cursor', String(recentQuery.data.value.next_cursor))"
          >
            较早记录
          </button>
        </div>
      </template>

      <template v-if="section === 'next' && nextEligibleQuery.data.value">
        <Panel
          v-if="nextEligibleQuery.data.value.items.length"
          eyebrow="SCHEDULE / NEXT"
          title="下一可用窗口"
        >
          <div class="trace-table-wrap">
            <table class="forensic-table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>目标</th>
                  <th>最近记录</th>
                  <th>下一可用时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in nextEligibleQuery.data.value.items" :key="`${item.scope}:${item.target}`">
                  <td><code>{{ item.scope }}</code></td>
                  <td><code>{{ item.target }}</code></td>
                  <td>{{ formatDateTime(item.latest_ts) }}</td>
                  <td>{{ formatDateTime(item.next_eligible_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>
        <div v-else class="empty-state" data-code="proactive_next_empty">
          当前没有带下一可用时间的记录。
        </div>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import type { ProactiveRecord } from "@/api/types";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { formatDateTime } from "@/lib/format";

const SCOPES = [
  { value: "", label: "全部" },
  { value: "private", label: "主动私聊" },
  { value: "group", label: "群主动接话" },
  { value: "qzone", label: "QQ 空间" },
];
const OUTCOME_OPTIONS = [
  { value: "", label: "全部结果" },
  { value: "sent", label: "已发送" },
  { value: "skip_llm_decided", label: "Agent 决定跳过" },
  { value: "skip_cooldown", label: "冷却中" },
  { value: "skip_probability", label: "概率门未通过" },
  { value: "skip_daily_limit", label: "达到每日上限" },
];

function outcomeLabel(value: string): string {
  if (value === "sent") return "已发送";
  if (value === "skip_llm_decided") return "Agent 决定跳过";
  if (value === "skip_cooldown") return "冷却中";
  if (value === "skip_probability") return "概率门未通过";
  if (value === "skip_daily_limit") return "达到每日上限";
  if (value === "skip_quiet_hour") return "静默时段";
  if (value === "skip_disabled") return "功能未启用";
  return value || "未分类";
}

function detailSummary(item: ProactiveRecord): string {
  const parts: string[] = [];
  const fields = [
    ["action", "动作"],
    ["len", "长度"],
    ["since_last_seconds", "距上次"],
    ["min_interval_minutes", "最短间隔"],
  ] as const;
  for (const [key, label] of fields) {
    const value = item.detail?.[key];
    if (typeof value === "string" || typeof value === "number") {
      parts.push(`${label}=${value}`);
    }
  }
  return parts.join(" · ") || "没有可见详情";
}

const route = useRoute();
const router = useRouter();

const section = computed(() => {
  const s = String(route.params.section ?? "");
  if (s === "overview" || route.path.endsWith("/overview")) return "overview";
  if (s === "next" || s === "next-eligible" || route.path.endsWith("/next-eligible")) return "next";
  return "recent";
});

const scope = computed(() => String(route.query.scope ?? ""));
const outcome = computed(() => String(route.query.outcome ?? ""));
const target = computed(() => String(route.query.target ?? ""));
const cursor = computed(() => Number(route.query.cursor ?? 0) || 0);

function setFilter(key: string, value: string) {
  const nextQuery = { ...route.query };
  if (value) {
    nextQuery[key] = value;
  } else {
    delete nextQuery[key];
  }
  if (key !== "cursor") {
    delete nextQuery.cursor;
  }
  router.push({ query: nextQuery });
}

const statsQuery = useQuery({
  queryKey: computed(() => ["proactive", "stats", scope.value]),
  queryFn: ({ signal }) => resources.proactiveStats(scope.value, signal),
  enabled: computed(() => section.value === "overview"),
});

const recentQuery = useQuery({
  queryKey: computed(() => ["proactive", "recent", scope.value, outcome.value, target.value, cursor.value]),
  queryFn: ({ signal }) =>
    resources.proactiveRecent(
      { scope: scope.value, outcome: outcome.value, target: target.value, cursor: cursor.value, limit: 50 },
      signal,
    ),
  enabled: computed(() => section.value === "recent"),
  placeholderData: (previousData) => previousData,
});

const nextEligibleQuery = useQuery({
  queryKey: computed(() => ["proactive", "next", scope.value]),
  queryFn: ({ signal }) => resources.proactiveNextEligible(scope.value, signal),
  enabled: computed(() => section.value === "next"),
});

const activeQuery = computed(() => {
  if (section.value === "overview") return statsQuery;
  if (section.value === "next") return nextEligibleQuery;
  return recentQuery;
});
</script>
