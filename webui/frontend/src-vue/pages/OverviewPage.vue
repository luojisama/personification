<template>
  <div class="page-stack">
    <PageHeader
      index="01"
      title="运行总览"
      description="了解机器人的当前状态，查看最近对话与需要关注的任务。"
    >
      <template #actions>
        <StateBadge :tone="realtime.state.value === 'open' ? 'ok' : 'running'">
          {{ realtime.state.value === 'open' ? 'SSE 在线' : 'SSE 连接中' }}
        </StateBadge>
      </template>
    </PageHeader>

    <QueryBoundary :pending="isPending" :error="error">
      <template v-if="data">
        <section class="overview-welcome">
          <div>
            <span class="welcome-label">运行概况</span>
            <h2>查看运行指标与待处理事项</h2>
            <p>核对路由能力、跟进恢复任务，或查看最近的回复记录。</p>
          </div>
          <nav class="welcome-links" aria-label="总览快捷入口">
            <RouterLink to="/runtime/routes/capabilities">
              <Icon name="route" />
              <span><b>路由能力</b><small>{{ formatInteger(data.route_counts.unknown) }} 项待核对</small></span>
              <Icon class="welcome-link-arrow" name="chevron" />
            </RouterLink>
            <RouterLink to="/runtime/recovery/pending">
              <Icon name="recovery" />
              <span><b>恢复队列</b><small>{{ formatInteger(data.recovery_counts.pending) }} 项待恢复</small></span>
              <Icon class="welcome-link-arrow" name="chevron" />
            </RouterLink>
          </nav>
        </section>
        <section class="metric-rack overview-metric-rack" aria-label="运行指标">
          <article>
            <span><Icon name="system" />运行状态</span>
            <strong>
              <StateBadge :tone="runtimeTone">
                {{ data.runtime_status === 'healthy' ? '健康' : data.runtime_status === 'offline' ? '离线' : '降级' }}
              </StateBadge>
            </strong>
            <small>快照 {{ formatDateTime(data.generated_at) }}</small>
          </article>
          <article>
            <span><Icon name="trace" />活跃回合</span>
            <strong>{{ formatInteger(data.active_turns) }}</strong>
            <small>当前进入调度链路</small>
          </article>
          <article>
            <span><Icon name="signal" />一小时事件</span>
            <strong>{{ formatInteger(data.events_last_hour) }}</strong>
            <small>仅脱敏运行事件</small>
          </article>
          <article>
            <span><Icon name="clock" />回合 p95</span>
            <strong>{{ formatDuration(data.p95_turn_ms) }}</strong>
            <small>不含管理异步任务</small>
          </article>
        </section>

        <div class="overview-grid">
          <Panel eyebrow="EVIDENCE / ROUTES" title="路由能力">
            <div class="evidence-bars">
              <div v-for="route in routeBreakdown" :key="route.key" :data-status="route.key">
                <span>{{ route.label }}</span>
                <b>{{ formatInteger(route.count) }}</b>
                <i aria-hidden="true"><span :style="{ width: route.share }" /></i>
              </div>
            </div>
            <RouterLink class="text-link" to="/runtime/routes/capabilities">
              核对每条路由的证据来源 →
            </RouterLink>
          </Panel>

          <Panel eyebrow="RECOVERY / INBOUND" title="恢复队列">
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
import Icon from "@vue-app/components/Icon.vue";
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

const routeBreakdown = computed(() => {
  const counts = data.value?.route_counts;
  if (!counts) return [];
  const total = counts.supported + counts.unknown + counts.unsupported;
  return ([
    { key: "supported", label: "支持" },
    { key: "unknown", label: "未知" },
    { key: "unsupported", label: "不支持" },
  ] as const).map(({ key, label }) => {
    const count = counts[key];
    return { key, label, count, share: `${total > 0 ? count / total * 100 : 0}%` };
  });
});
</script>
