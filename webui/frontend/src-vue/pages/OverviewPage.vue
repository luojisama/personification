<template>
  <div class="page-stack">
    <PageHeader
      index="01"
      title="事件总览"
      description="从回复回合、路由证据和恢复队列中提取当前风险；实时流只做增量提示，数据库仍是权威记录。"
    >
      <template #actions>
        <StateBadge :tone="realtime.state.value === 'open' ? 'ok' : 'running'">
          {{ realtime.state.value === 'open' ? 'SSE 在线' : 'SSE 连接中' }}
        </StateBadge>
      </template>
    </PageHeader>

    <QueryBoundary :pending="isPending" :error="error">
      <template v-if="data">
        <section class="metric-rack" aria-label="运行指标">
          <article>
            <span>运行状态</span>
            <strong>
              <StateBadge :tone="runtimeTone">
                {{ data.runtime_status === 'healthy' ? '健康' : data.runtime_status === 'offline' ? '离线' : '降级' }}
              </StateBadge>
            </strong>
            <small>快照 {{ formatDateTime(data.generated_at) }}</small>
          </article>
          <article>
            <span>活跃回合</span>
            <strong>{{ formatInteger(data.active_turns) }}</strong>
            <small>当前进入调度链路</small>
          </article>
          <article>
            <span>一小时事件</span>
            <strong>{{ formatInteger(data.events_last_hour) }}</strong>
            <small>仅脱敏运行事件</small>
          </article>
          <article>
            <span>回合 p95</span>
            <strong>{{ formatDuration(data.p95_turn_ms) }}</strong>
            <small>不含管理异步任务</small>
          </article>
        </section>

        <div class="overview-grid">
          <Panel eyebrow="EVIDENCE / ROUTES" title="路由能力证据">
            <div class="evidence-bars">
              <div>
                <span>支持</span>
                <b>{{ formatInteger(data.route_counts.supported) }}</b>
                <i :style="{ '--bar': `${data.route_counts.supported}` }" />
              </div>
              <div>
                <span>未知</span>
                <b>{{ formatInteger(data.route_counts.unknown) }}</b>
                <i :style="{ '--bar': `${data.route_counts.unknown}` }" />
              </div>
              <div>
                <span>不支持</span>
                <b>{{ formatInteger(data.route_counts.unsupported) }}</b>
                <i :style="{ '--bar': `${data.route_counts.unsupported}` }" />
              </div>
            </div>
            <RouterLink class="text-link" to="/runtime/routes/capabilities">
              核对每条路由的证据来源 →
            </RouterLink>
          </Panel>

          <Panel eyebrow="RECOVERY / INBOUND" title="失败恢复队列">
            <dl class="count-ledger">
              <div><dt>待恢复</dt><dd>{{ formatInteger(data.recovery_counts.pending) }}</dd></div>
              <div><dt>处理中</dt><dd>{{ formatInteger(data.recovery_counts.processing) }}</dd></div>
              <div><dt>人工核对区</dt><dd>{{ formatInteger(data.recovery_counts.quarantined) }}</dd></div>
              <div><dt>已过期</dt><dd>{{ formatInteger(data.recovery_counts.expired) }}</dd></div>
            </dl>
            <RouterLink class="text-link" to="/runtime/recovery/pending">
              进入恢复卷宗 →
            </RouterLink>
          </Panel>

          <Panel class="wide-panel" eyebrow="TRACE / LATEST" title="最近回合">
            <EmptyState v-if="data.latest_traces.length === 0" code="trace_list_empty">
              当前没有可展示的 Trace。
            </EmptyState>
            <div v-else class="trace-table-wrap">
              <table class="forensic-table">
                <thead>
                  <tr>
                    <th>开始时间</th>
                    <th>Trace ID</th>
                    <th>用户</th>
                    <th>结果</th>
                    <th>耗时</th>
                    <th>诊断码</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="trace in data.latest_traces.slice(0, 8)" :key="trace.trace_id">
                    <td>{{ formatDateTime(trace.started_at) }}</td>
                    <td>
                      <RouterLink :to="`/runtime/traces/timeline/${trace.trace_id}`">
                        <code>{{ shortId(trace.trace_id, 6) }}</code>
                      </RouterLink>
                    </td>
                    <td>{{ trace.user_name || trace.user_id }}</td>
                    <td>{{ traceOutcomeLabel(trace.outcome) }}</td>
                    <td>{{ formatDuration(trace.elapsed_ms) }}</td>
                    <td><code>{{ trace.diagnosis_code }}</code></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel class="wide-panel" eyebrow="DIAGNOSTICS / OPEN" title="待核对诊断">
            <EmptyState v-if="data.diagnostics.length === 0" code="diagnostic_list_empty">
              没有未处理的运行诊断。
            </EmptyState>
            <ul v-else class="alert-ledger">
              <li v-for="item in data.diagnostics" :key="`${item.code}:${item.trace_id ?? ''}`" :data-level="item.level">
                <span>{{ item.title }}</span>
                <code>{{ item.code }}</code>
                <small v-if="item.trace_id">Trace {{ shortId(item.trace_id) }}</small>
              </li>
            </ul>
          </Panel>
        </div>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import { useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import { formatDateTime, formatDuration, formatInteger, shortId } from "@/lib/format";
import { traceOutcomeLabel } from "@/lib/labels";
import EmptyState from "@vue-app/components/EmptyState.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import { useRuntimeEvents } from "@vue-app/realtime/runtimeEvents";

const realtime = useRuntimeEvents();
const { data, isPending, error } = useQuery({
  queryKey: ["overview"],
  queryFn: ({ signal }) => resources.overview(signal),
});

const runtimeTone = computed(() => {
  if (!data.value) return "unknown";
  return data.value.runtime_status === "healthy" ? "ok" : data.value.runtime_status === "offline" ? "error" : "warn";
});
</script>
