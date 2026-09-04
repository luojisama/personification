<template>
  <div class="page-stack trace-page">
    <PageHeader
      index="08"
      title="Trace 取证"
      description="只展示可审计决策摘要、状态机、阶段预算和脱敏工具证据；不请求、不存储也不渲染模型隐藏思维链。"
    >
      <template #actions>
        <TextField
          v-model="search"
          class="search-field"
          label="搜索 Trace ID、会话、QQ、状态或诊断码"
          hide-label
          type="search"
          placeholder="搜索 Trace ID、会话、QQ、状态或诊断码"
          @update:model-value="page = 1"
        />
      </template>
    </PageHeader>

    <div class="trace-workbench">
      <Panel class="trace-list-pane" eyebrow="INDEX / TURNS" title="回合索引">
        <QueryBoundary
          :pending="listQuery.isPending.value"
          :error="listQuery.error.value"
          :empty="Boolean(listQuery.data.value && listQuery.data.value.items.length === 0)"
          empty-text="没有匹配的 Trace。"
        >
          <div v-if="listQuery.data.value?.items.length" class="trace-index-list">
            <RouterLink
              v-for="item in listQuery.data.value.items"
              :key="item.trace_id"
              :to="`/runtime/traces/timeline/${encodeURIComponent(item.trace_id)}`"
              :class="{ active: item.trace_id === currentTraceId }"
            >
              <div class="trace-index-head">
                <code>{{ shortId(item.trace_id, 6) }}</code>
                <time>{{ formatDateTime(item.started_at) }}</time>
              </div>
              <strong>{{ item.user_name || item.user_id }}</strong>
              <p>{{ item.input_summary || "没有可展示的消息摘要" }}</p>
              <div class="trace-index-meta">
                <StateBadge :tone="outcomeTone(item.outcome)">{{ traceOutcomeLabel(item.outcome) }}</StateBadge>
                <span>{{ formatDuration(item.elapsed_ms) }}</span>
              </div>
            </RouterLink>
          </div>
        </QueryBoundary>

        <div v-if="listQuery.data.value && listQuery.data.value.total_pages > 1" class="pagination">
          <button type="button" :disabled="page <= 1" @click="page--">上一页</button>
          <span>{{ listQuery.data.value.page }} / {{ listQuery.data.value.total_pages }}</span>
          <button type="button" :disabled="page >= listQuery.data.value.total_pages" @click="page++">下一页</button>
        </div>
      </Panel>

      <QueryBoundary
        :pending="Boolean(currentTraceId) && detailQuery.isPending.value"
        :error="detailQuery.error.value"
      >
        <template v-if="detailQuery.data.value">
          <Panel class="trace-timeline-pane" eyebrow="TIMELINE / OBSERVABLE" title="阶段时间线">
            <div class="trace-case-head">
              <div class="avatar-stamp" aria-hidden="true">
                <img
                  v-if="detailQuery.data.value.avatar_url"
                  :src="detailQuery.data.value.avatar_url"
                  alt=""
                  referrerpolicy="no-referrer"
                />
                <template v-else>{{ (detailQuery.data.value.user_name || "?").slice(0, 1) }}</template>
              </div>
              <div>
                <strong>{{ detailQuery.data.value.user_name || "未知用户" }}</strong>
                <span>QQ {{ detailQuery.data.value.user_id || "—" }} · {{ sessionTypeLabel(detailQuery.data.value.session_type) }}</span>
              </div>
              <StateBadge :tone="outcomeTone(detailQuery.data.value.outcome)" :raw="detailQuery.data.value.outcome">
                {{ traceOutcomeLabel(detailQuery.data.value.outcome) }}
              </StateBadge>
            </div>

            <article class="message-evidence">
              <span>收到的消息 · 安全摘要</span>
              <p>{{ detailQuery.data.value.input_summary || "消息内容未进入可见 Trace。" }}</p>
              <ul v-if="detailQuery.data.value.media_summary.length > 0">
                <li v-for="(media, idx) in detailQuery.data.value.media_summary" :key="`${idx}:${media}`">{{ media }}</li>
              </ul>
            </article>

            <section class="trace-triage" aria-labelledby="trace-triage-title">
              <div>
                <span>TRIAGE / FIRST FAILURE</span>
                <h3 id="trace-triage-title">本轮诊断摘要</h3>
                <p>{{ triageSummary }}</p>
              </div>
              <dl>
                <div>
                  <dt>错误与告警</dt>
                  <dd>{{ derivedMetrics.issueCount }}</dd>
                </div>
                <div>
                  <dt>成功返回的工具结果</dt>
                  <dd>{{ derivedMetrics.completedToolCount }}</dd>
                </div>
                <div>
                  <dt>首个错误</dt>
                  <dd>{{ derivedMetrics.firstErrorIndex === null ? "无" : `阶段 ${derivedMetrics.firstErrorIndex + 1}` }}</dd>
                </div>
              </dl>
              <button
                v-if="derivedMetrics.firstErrorIndex !== null"
                type="button"
                class="button button-quiet"
                @click="jumpToFirstError"
              >
                跳到首个错误
              </button>
            </section>

            <div class="trace-stage-toolbar">
              <div class="filter-chips" aria-label="时间线筛选">
                <button type="button" :aria-pressed="stageFilter === 'all'" @click="stageFilter = 'all'">全部 {{ detailQuery.data.value.stages.length }}</button>
                <button type="button" :aria-pressed="stageFilter === 'issues'" @click="stageFilter = 'issues'">问题 {{ derivedMetrics.issueCount }}</button>
                <button type="button" :aria-pressed="stageFilter === 'slow'" @click="stageFilter = 'slow'">最慢 {{ derivedMetrics.slowStageIndexes.length }}</button>
              </div>
              <span>筛选只改变展示，不改变原始 Trace。</span>
            </div>

            <ol class="timeline-list">
              <li
                v-for="{ stage, index } in visibleStages"
                :id="`trace-stage-${index}`"
                :key="`${stage.key}:${index}`"
                tabindex="-1"
                :data-status="stage.status"
              >
                <span class="timeline-node" aria-hidden="true" />
                <div class="timeline-card">
                  <header>
                    <div>
                      <span>{{ String(index + 1).padStart(2, "0") }}</span>
                      <strong>{{ stage.label }}</strong>
                    </div>
                    <StateBadge :tone="stageTone(stage.status)" :raw="stage.status">
                      {{ stageStatusLabel(stage.status) }}
                    </StateBadge>
                  </header>
                  <p v-if="stage.summary">{{ stage.summary }}</p>
                  <footer>
                    <code>{{ stage.detail_code }}</code>
                    <span>{{ formatDuration(stage.duration_ms) }}</span>
                    <span v-if="stage.remaining_ms !== null">剩余预算 {{ formatDuration(stage.remaining_ms) }}</span>
                  </footer>
                </div>
              </li>
            </ol>
            <p v-if="visibleStages.length === 0" class="muted trace-filter-empty">当前筛选下没有阶段。</p>
          </Panel>

          <Panel class="trace-detail-pane" eyebrow="CONTEXT / AUDIT" title="审计详情">
            <section class="audit-section">
              <h3>Agent 决策摘要</h3>
              <p>{{ detailQuery.data.value.decision.summary || "本轮没有可展示的结构化决策摘要。" }}</p>
              <dl class="audit-grid">
                <div><dt>动作</dt><dd>{{ detailQuery.data.value.decision.action }}</dd></div>
                <div><dt>参与等级</dt><dd>{{ detailQuery.data.value.decision.tier ?? "—" }}</dd></div>
                <div><dt>等待</dt><dd>{{ detailQuery.data.value.decision.wait_seconds === null ? "—" : `${detailQuery.data.value.decision.wait_seconds} 秒` }}</dd></div>
                <div><dt>兴趣</dt><dd>{{ detailQuery.data.value.decision.interest === null ? "—" : detailQuery.data.value.decision.interest.toFixed(2) }}</dd></div>
              </dl>
              <code>{{ detailQuery.data.value.decision.reason_code }}</code>
            </section>

            <section class="audit-section">
              <h3>工具步骤</h3>
              <p v-if="detailQuery.data.value.tools.length === 0" class="muted">本轮没有脱敏工具记录。</p>
              <template v-else>
                <details v-for="(tool, idx) in detailQuery.data.value.tools" :key="`${tool.name}:${idx}`" class="tool-evidence">
                  <summary>
                    <span>{{ tool.namespace }} / {{ tool.name }}</span>
                    <StateBadge :tone="tool.status === 'ok' ? 'ok' : 'warn'" :raw="tool.status">
                      {{ tool.status === 'ok' ? '完成' : '需核对' }}
                    </StateBadge>
                  </summary>
                  <dl>
                    <div><dt>参数摘要</dt><dd>{{ tool.argument_summary || "未记录" }}</dd></div>
                    <div><dt>结果摘要</dt><dd>{{ tool.result_summary || "未记录" }}</dd></div>
                    <div><dt>Schema hash</dt><dd><code>{{ tool.schema_hash || "—" }}</code></dd></div>
                    <div><dt>诊断码</dt><dd><code>{{ tool.detail_code }}</code></dd></div>
                  </dl>
                </details>
              </template>
            </section>

            <section class="audit-section final-output-evidence">
              <h3>最终可见回复</h3>
              <blockquote>{{ detailQuery.data.value.final_reply || "本轮没有发送可见回复。" }}</blockquote>
              <dl class="audit-grid">
                <div><dt>发送结果</dt><dd>{{ traceDeliveryStatusLabel(detailQuery.data.value.send_status) }}</dd></div>
                <div><dt>历史提交</dt><dd>{{ traceHistoryStatusLabel(detailQuery.data.value.history_status) }}</dd></div>
              </dl>
            </section>

            <section class="audit-section trace-identifiers">
              <h3>关联标识</h3>
              <dl>
                <dt>Trace ID</dt><dd><code>{{ detailQuery.data.value.trace_id }}</code></dd>
                <dt>Bot ID</dt><dd><code>{{ detailQuery.data.value.bot_id || "—" }}</code></dd>
                <dt>诊断码</dt><dd><code>{{ detailQuery.data.value.diagnosis_code }}</code></dd>
                <dt>恢复项</dt><dd>{{ detailQuery.data.value.recovery_ids.length ? detailQuery.data.value.recovery_ids.join("、") : "无" }}</dd>
              </dl>
            </section>
          </Panel>
        </template>
        <div v-else class="empty-state">
          <p class="empty-notice">从左侧选择一条 Trace 开始核对。</p>
        </div>
      </QueryBoundary>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import type { TraceStage } from "@/api/types";
import { formatDateTime, formatDuration, shortId } from "@/lib/format";
import {
  sessionTypeLabel,
  traceDeliveryStatusLabel,
  traceHistoryStatusLabel,
  traceOutcomeLabel,
} from "@/lib/labels";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { deriveTraceMetrics, outcomeTone, stageStatusLabel, stageTone, traceTriageText, type StageFilter } from "./tracesPageMetrics";

const route = useRoute();
const router = useRouter();

const page = ref(1);
const search = ref("");
const debouncedSearch = ref("");
const stageFilter = ref<StageFilter>("all");
let searchTimer: number | undefined;

const currentTraceId = computed(() => (typeof route.params.traceId === "string" ? route.params.traceId : ""));

const listQuery = useQuery({
  queryKey: computed(() => ["traces", page.value, debouncedSearch.value]),
  queryFn: ({ signal }) => resources.traces(page.value, 20, debouncedSearch.value, signal),
});

watch(search, (value) => {
  if (searchTimer !== undefined) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    debouncedSearch.value = value.trim();
  }, 250);
});

onBeforeUnmount(() => {
  if (searchTimer !== undefined) window.clearTimeout(searchTimer);
});

const detailQuery = useQuery({
  queryKey: computed(() => ["trace-detail", currentTraceId.value]),
  queryFn: ({ signal }) => resources.trace(currentTraceId.value, signal),
  enabled: computed(() => Boolean(currentTraceId.value)),
});

watch(
  () => listQuery.data.value,
  (data) => {
    if (!currentTraceId.value && data?.items[0]?.trace_id) {
      void router.replace(`/runtime/traces/timeline/${encodeURIComponent(data.items[0].trace_id)}`);
    }
  },
  { immediate: true },
);

watch(currentTraceId, () => {
  stageFilter.value = "all";
});

const derivedMetrics = computed(() =>
  detailQuery.data.value ? deriveTraceMetrics(detailQuery.data.value) : {
    issueCount: 0,
    completedToolCount: 0,
    firstErrorIndex: null,
    slowStageIndexes: [],
    upstreamStatus: "",
    upstreamDetailCode: "",
  },
);

const triageSummary = computed(() =>
  detailQuery.data.value ? traceTriageText(detailQuery.data.value, derivedMetrics.value) : "",
);

const visibleStages = computed(() => {
  if (!detailQuery.data.value) return [];
  return detailQuery.data.value.stages
    .map((stage: TraceStage, index: number) => ({ stage, index }))
    .filter(({ stage, index }: { stage: TraceStage; index: number }) => {
      if (stageFilter.value === "issues") return stage.status === "warn" || stage.status === "error";
      if (stageFilter.value === "slow") return derivedMetrics.value.slowStageIndexes.includes(index);
      return true;
    });
});

function jumpToFirstError() {
  if (derivedMetrics.value.firstErrorIndex === null) return;
  stageFilter.value = "all";
  void nextTick(() => {
    const target = document.getElementById(`trace-stage-${derivedMetrics.value.firstErrorIndex}`);
    if (!target) return;
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    target.focus({ preventScroll: true });
  });
}
</script>
