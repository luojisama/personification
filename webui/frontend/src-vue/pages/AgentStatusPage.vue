<template>
  <div class="page-stack">
    <PageHeader
      index="02"
      title="Agent 状态"
      description="实时汇总 Bot 连接、回复回合、内心状态、进程资源和最近 Trace。只展示可审计状态，不展示隐藏思维链。"
    >
      <template #actions>
        <button class="button button-secondary" type="button" @click="refetch()">
          立即刷新
        </button>
      </template>
    </PageHeader>

    <QueryBoundary :pending="isPending" :error="error">
      <template v-if="data">
        <Panel eyebrow="BOT / RUNTIME" title="当前 Bot 与 Agent">
          <div class="agent-identity-line">
            <IdentityAvatar :src="data.bot.avatar_url" :label="data.bot.nickname" size="large" />
            <div>
              <strong>{{ data.bot.nickname }}</strong>
              <code>QQ {{ data.bot.bot_id || '未连接' }}</code>
            </div>
            <StateBadge :tone="data.bot.online ? 'ok' : 'error'">
              {{ data.bot.online ? '协议端在线' : '协议端离线' }}
            </StateBadge>
            <StateBadge :tone="data.enabled ? 'ok' : 'warn'">
              {{ data.enabled ? 'Agent 已启用' : 'Agent 已停用' }}
            </StateBadge>
            <span>最后活动 {{ formatDateTime(data.last_active_at) }}</span>
          </div>
        </Panel>

        <section class="metric-rack" aria-label="回合状态">
          <article><span>并发排队</span><strong>{{ data.admission_waiting_turns }}</strong><small>等待并发准入</small></article>
          <article><span>缓冲会话 / 消息</span><strong>{{ data.buffered_sessions }} / {{ data.buffered_messages }}</strong><small>最久等待 {{ formatDuration(data.oldest_buffer_age_ms) }}</small></article>
          <article><span>正在生成</span><strong>{{ data.active_turns }}</strong><small>当前活动回复任务</small></article>
          <article><span>正在发送</span><strong>{{ data.sending_turns }}</strong><small>进入发送/确认阶段</small></article>
          <article><span>停滞回合</span><strong>{{ data.stale_turns }}</strong><small>{{ data.cancelled_turns }} 次取消 · {{ data.gated_turns }} 个 gate</small></article>
        </section>

        <div class="overview-grid">
          <Panel eyebrow="INNER STATE" title="可观察内心状态">
            <dl class="safe-settings-view">
              <div><dt>心情</dt><dd>{{ data.inner_state.mood || '未记录' }}</dd></div>
              <div><dt>精力</dt><dd>{{ data.inner_state.energy || '未记录' }}</dd></div>
              <div><dt>待处理状态</dt><dd>{{ data.inner_state.pending_count }}</dd></div>
              <div><dt>更新时间</dt><dd>{{ data.inner_state.updated_at || '—' }}</dd></div>
            </dl>
          </Panel>

          <Panel eyebrow="PROCESS / LATENCY" title="运行性能">
            <dl class="safe-settings-view">
              <div><dt>当前 / 峰值内存</dt><dd>{{ bytes(data.rss_bytes) }} / {{ bytes(data.peak_rss_bytes) }}</dd></div>
              <div><dt>事件循环 p50 / p95</dt><dd>{{ data.event_loop_p50_ms ?? '—' }} / {{ data.event_loop_p95_ms ?? '—' }} ms</dd></div>
              <div><dt>回合 p50 / p95</dt><dd>{{ formatDuration(data.turn_p50_ms) }} / {{ formatDuration(data.turn_p95_ms) }}</dd></div>
              <div><dt>后台任务</dt><dd>{{ data.background_tasks }} 个 · {{ data.background_failures }} 次失败 · 缓存 {{ formatInteger(data.cache_entries) }}</dd></div>
            </dl>
          </Panel>

          <Panel eyebrow="PROVIDER STREAMING" title="上游流式缓冲">
            <dl class="safe-settings-view">
              <div>
                <dt>流式模式</dt>
                <dd>
                  <StateBadge :tone="data.provider_streaming?.mode === 'buffered' ? 'ok' : 'unknown'">
                    {{ streamingModeLabel(data.provider_streaming?.mode) }}
                  </StateBadge>
                </dd>
              </div>
              <div>
                <dt>当前路由支持</dt>
                <dd>
                  <StateBadge :tone="routeSupportTone(data.provider_streaming?.route_supported)">
                    {{ routeSupportLabel(data.provider_streaming?.route_supported) }}
                  </StateBadge>
                </dd>
              </div>
              <div><dt>活跃调用</dt><dd>{{ data.provider_streaming?.active_calls ?? 0 }}</dd></div>
              <div><dt>回退次数</dt><dd>{{ data.provider_streaming?.fallback_count ?? 0 }}</dd></div>
              <div><dt>首块延迟</dt><dd>{{ formatDuration(data.provider_streaming?.first_chunk_ms) }}</dd></div>
              <div><dt>总耗时</dt><dd>{{ formatDuration(data.provider_streaming?.total_ms) }}</dd></div>
              <div><dt>Chunk 数</dt><dd>{{ data.provider_streaming?.chunk_count ?? 0 }}</dd></div>
              <div><dt>交付保证</dt><dd>缓冲模式仅在内存组装完整内容，QQ 端在工具循环和审阅完成后才展示完整气泡</dd></div>
            </dl>
          </Panel>

          <Panel class="wide-panel" eyebrow="RECENT TURNS" title="最近回合">
            <div class="trace-table-wrap">
              <table class="forensic-table">
                <thead>
                  <tr>
                    <th>结果</th>
                    <th>Trace</th>
                    <th>耗时</th>
                    <th>模型</th>
                    <th>工具</th>
                    <th>会话</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="trace in data.recent_traces" :key="trace.trace_id">
                    <td>
                      <StateBadge :tone="trace.state === 'stale' ? 'warn' : trace.outcome === 'ok' ? 'ok' : 'unknown'" :raw="trace.outcome">
                        {{ trace.outcome }}
                      </StateBadge>
                    </td>
                    <td>
                      <RouterLink :to="`/runtime/traces/timeline/${trace.trace_id}`">
                        <code>{{ trace.trace_id }}</code>
                      </RouterLink>
                    </td>
                    <td>{{ formatDuration(trace.elapsed_ms) }}</td>
                    <td>{{ trace.model || '未记录' }}</td>
                    <td>{{ trace.tool_count }}</td>
                    <td>{{ trace.session_type }}{{ trace.group_id ? ` · ${trace.group_id}` : '' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from "pinia";
import { RouterLink } from "vue-router";
import { useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import { formatDateTime, formatDuration, formatInteger } from "@/lib/format";
import IdentityAvatar from "@vue-app/components/IdentityAvatar.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import { useBotStore } from "@vue-app/stores/bot";

const botStore = useBotStore();
const { selectedBotId } = storeToRefs(botStore);

const { data, isPending, error, refetch } = useQuery({
  queryKey: ["agent-runtime", selectedBotId],
  queryFn: ({ signal }) => resources.agentRuntime(selectedBotId.value, signal),
  refetchInterval: () => (typeof document !== "undefined" && document.hidden ? false : 5_000),
});

function bytes(value: number | null | undefined): string {
  return value == null ? "暂不可用" : `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function streamingModeLabel(mode?: string): string {
  if (mode === "buffered") return "缓冲";
  if (mode === "off" || !mode) return "关闭";
  return `未知模式（${mode}）`;
}

function routeSupportTone(supported?: boolean | string | null): "ok" | "warn" | "unknown" {
  if (supported === true || supported === "supported") return "ok";
  if (supported === false || supported === "unsupported") return "warn";
  return "unknown";
}

function routeSupportLabel(supported?: boolean | string | null): string {
  if (supported === true || supported === "supported") return "支持";
  if (supported === false || supported === "unsupported") return "不支持";
  return "未知";
}
</script>
