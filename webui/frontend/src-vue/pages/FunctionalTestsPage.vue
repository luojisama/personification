<template>
  <div class="page-stack">
    <PageHeader
      index="04"
      title="功能体检"
      description="区分本地只读检查、Provider 外部读取探针与真实 rules→buffer→model→review→ledger→send QQ canary。源码与自动测试绝不发送真实 QQ。"
    />

    <!-- 结构化诊断告警面板 -->
    <section v-if="activeDiagnostic" class="panel diagnostic-alert-panel" role="region" aria-label="诊断详情">
      <header class="panel-heading">
        <div class="panel-heading-copy">
          <div class="panel-eyebrow">体检诊断</div>
          <h2 class="panel-title">{{ activeDiagnostic.title }} ({{ activeDiagnostic.code }})</h2>
        </div>
        <div class="panel-actions">
          <button type="button" class="button button-ghost" @click="clearError">关闭</button>
        </div>
      </header>
      <div class="panel-body">
        <p class="diagnostic-message">{{ activeDiagnostic.message }}</p>
        <div v-if="activeDiagnostic.suggestion" class="diagnostic-suggestion">
          <strong>处理建议：</strong>{{ activeDiagnostic.suggestion }}
        </div>
        <div v-if="activeDiagnostic.trace_id" class="diagnostic-meta">
          <span>Trace ID: <code>{{ activeDiagnostic.trace_id }}</code></span>
        </div>
      </div>
    </section>

    <QueryBoundary :pending="catalogQuery.isPending.value" :error="catalogQuery.error.value">
      <template v-if="catalogQuery.data.value">
        <Panel eyebrow="RISK-GRADED TESTS" title="体检项目">
          <p class="test-category">
            本页只运行受控体检。QQ 与 QZone 外部写入不会在此页面执行；真实单目标 canary 需在专用入口经管理员确认并留下可对账的 Trace。
          </p>
          <section v-for="group in testGroups" :key="group.name" class="health-test-group">
            <header class="test-card-header">
              <div>
                <strong>{{ group.name }}</strong>
                <small class="test-category">{{ group.tests.length }} 个体检项目</small>
              </div>
            </header>
            <div class="health-test-grid">
              <article
                v-for="test in group.tests"
                :key="test.id"
                class="health-test-card"
              >
                <header class="test-card-header">
                  <div>
                    <strong>{{ test.label }}</strong>
                    <small class="test-category">{{ test.category }}</small>
                  </div>
                  <div>
                    <StateBadge
                      :tone="test.risk === 'external_write' ? 'warn' : test.risk === 'external_read' ? 'warn' : 'ok'"
                    >
                      {{ RISK_LABELS[test.risk] }}
                    </StateBadge>
                    <StateBadge tone="unknown" :raw="test.execution_kind">
                      {{ EXECUTION_LABELS[test.execution_kind] }}
                    </StateBadge>
                  </div>
                </header>
                <p class="test-category">{{ timeoutBoundaryLabel(test) }}</p>

                <div v-if="test.risk === 'external_write'" class="test-target-field">
                  <TextField
                    :id="'target-' + test.id"
                    v-model="targets[test.id]"
                    label="目标复核摘要"
                    type="text"
                    :placeholder="'Bot、目标 QQ/群或动态 ID'"
                    description="本页不会发送 QQ 或写入 QZone；公网 HTTP 模式也会由服务端强制拒绝外部写操作。"
                  />
                </div>

                <div class="test-card-actions">
                  <button
                    type="button"
                    class="button button-secondary"
                    :disabled="isTestBusy(test.id)"
                    @click="handleRunRequest(test)"
                  >
                    {{ runButtonLabel(test) }}
                  </button>
                </div>

                <template v-for="run in [runs[test.id]]" :key="`run:${test.id}`">
                  <div v-if="run" class="test-run-result">
                    <div class="result-status-row">
                      <StateBadge :tone="getRunTone(run.state)" :raw="run.state">
                        {{ RUN_STATE_LABELS[run.state] }}
                      </StateBadge>
                      <code class="diagnostic-code-pill">{{ run.diagnostic_code }}</code>
                      <span class="result-time-meta">
                        {{ formatDuration(run.duration_ms) }} · {{ formatDateTime(run.finished_at) }}
                      </span>
                    </div>
                    <dl class="structured-summary-list">
                      <div class="summary-kv-pair"><dt>执行方式</dt><dd>{{ EXECUTION_LABELS[run.execution_kind] || run.execution_kind }}</dd></div>
                      <div class="summary-kv-pair"><dt>开始</dt><dd>{{ formatDateTime(run.started_at) }}</dd></div>
                      <div class="summary-kv-pair"><dt>结束</dt><dd>{{ formatDateTime(run.finished_at) }}</dd></div>
                      <div class="summary-kv-pair"><dt>耗时</dt><dd>{{ formatDuration(run.duration_ms) }}</dd></div>
                      <div v-if="run.trace_id" class="summary-kv-pair"><dt>Trace</dt><dd><code>{{ run.trace_id }}</code></dd></div>
                      <div class="summary-kv-pair"><dt>交付</dt><dd>{{ DELIVERY_LABELS[run.delivery_status] || run.delivery_status }}</dd></div>
                    </dl>
                    <section v-if="run.diagnostic" class="diagnostic-suggestion">
                      <strong>{{ run.diagnostic.title }}：</strong>{{ run.diagnostic.message }}
                    </section>
                    <ol v-if="run.steps.length" class="structured-summary-list">
                      <li v-for="step in run.steps" :key="step.key">
                        <strong>{{ step.label }}</strong>：{{ stepStatusLabel(step.status) }}<template v-if="step.message"> · {{ step.message }}</template>
                      </li>
                    </ol>
                    <dl v-if="hasSummaryEntries(run)" class="structured-summary-list">
                      <div v-for="(val, key) in run.result_summary" :key="key" class="summary-kv-pair">
                        <dt>{{ key }}</dt>
                        <dd>{{ formatSummaryValue(val) }}</dd>
                      </div>
                    </dl>
                  </div>
                </template>
              </article>
            </div>
          </section>
        </Panel>

        <div v-if="!catalogQuery.data.value.cached" class="empty-state-notice">
          <p>尚无全量体检缓存；可按项目运行检查。</p>
        </div>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from "vue";
import { useQuery, useMutation } from "@tanstack/vue-query";

import { diagnosticFromError } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { FunctionalTestDefinition, FunctionalTestRun, OperationDiagnostic } from "@/api/types";
import { formatDateTime, formatDuration } from "@/lib/format";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextField from "@vue-app/components/forms/TextField.vue";

const RISK_LABELS = {
  local_read: "本地只读",
  external_read: "外部读取",
  external_write: "外部写入",
} as const;

const GROUP_ORDER = ["核心运行", "模型与媒体", "存储与记忆", "QQ 与群聊", "QZone", "后台任务与权限"] as const;

const EXECUTION_LABELS = {
  local_readonly: "本地只读检查",
  provider_probe: "Provider 外部读取探针",
  qq_canary: "真实 QQ canary（专用入口）",
  qzone_canary: "QZone canary（专用入口）",
} as const;

const RUN_STATE_LABELS = {
  prepared: "已准备",
  awaiting_confirmation: "等待确认",
  running: "运行中",
  succeeded: "已完成",
  failed: "未通过",
  unknown: "结果未知",
} as const;

const DELIVERY_LABELS: Record<string, string> = {
  not_applicable: "不适用（本次未交付）",
  not_started: "未开始",
  dedicated_canary_required: "需要专用 canary",
};

const catalogQuery = useQuery({
  queryKey: ["functional-health"],
  queryFn: ({ signal }) => resources.health(signal),
});

const runs = ref<Record<string, FunctionalTestRun>>({});
const targets = ref<Record<string, string>>({});
const currentError = ref<unknown>(null);

const activeDiagnostic = computed<OperationDiagnostic | null>(() => {
  if (currentError.value == null) return null;
  return diagnosticFromError(currentError.value);
});

const testGroups = computed(() => {
  const tests = catalogQuery.data.value?.tests ?? [];
  return GROUP_ORDER.map((name) => ({
    name,
    tests: tests.filter((test) => test.group === name),
  })).filter((group) => group.tests.length > 0);
});

function clearError() {
  currentError.value = null;
}

const runMutation = useMutation({
  mutationFn: async (test: FunctionalTestDefinition) => {
    const target = targets.value[test.id] ?? "";
    const prepared = await resources.prepareTestRun(test.id, target);
    if (prepared.state !== "awaiting_confirmation") {
      return prepared;
    }
    return resources.confirmTestRun(prepared.id, target);
  },
  onSuccess: (run) => {
    currentError.value = null;
    runs.value = { ...runs.value, [run.test_id]: run };
  },
  onError: (err) => {
    currentError.value = err;
  },
});

function handleRunRequest(test: FunctionalTestDefinition) {
  const target = targets.value[test.id] ?? "";
  if (test.risk === "external_write") {
    const detail = `将准备外部写操作，目标为：${target || "未填写"}。本页不会绕过专用 canary 的目标复核。\n\n确认继续吗？`;
    if (!window.confirm(detail)) {
      return; // 明确取消，不发起任何 API 请求
    }
  } else if (test.risk === "external_read") {
    const detail = `将调用 ${test.label} 对应的外部服务，可能产生供应商额度消耗。\n\n确认继续吗？`;
    if (!window.confirm(detail)) {
      return; // 明确取消，不发起任何 API 请求
    }
  }
  runMutation.mutate(test);
}

function isTestBusy(testId: string): boolean {
  if (runMutation.isPending.value) return true;
  const r = runs.value[testId];
  return r?.state === "prepared" || r?.state === "running";
}

function runButtonLabel(test: FunctionalTestDefinition): string {
  if (isTestBusy(test.id)) return "运行中…";
  if (test.risk === "local_read") return "运行本地检查";
  if (test.execution_kind === "qq_canary" || test.execution_kind === "qzone_canary") return "查看专用 canary 要求";
  return "准备并确认";
}

function timeoutBoundaryLabel(test: FunctionalTestDefinition): string {
  if (test.execution_kind === "provider_probe") {
    return "超时边界：Provider 探针受诊断安全时限控制；超时只会标记为结果未知。";
  }
  if (test.execution_kind === "qq_canary" || test.execution_kind === "qzone_canary") {
    return "超时边界：本页不启动真实 canary；专用入口必须记录超时与 Trace。";
  }
  return "超时边界：本地只读检查仅记录实际耗时，不发送 QQ。";
}

function getRunTone(state: FunctionalTestRun["state"]): "ok" | "warn" | "error" | "running" | "unknown" {
  if (state === "succeeded") return "ok";
  if (state === "failed") return "error";
  if (state === "prepared" || state === "running") return "running";
  if (state === "unknown") return "unknown";
  return "unknown";
}

function stepStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "等待中",
    ok: "完成",
    warn: "有告警",
    error: "失败",
    skipped: "已跳过",
    unknown: "结果未知",
  };
  return labels[status] || "结果未知";
}

function hasSummaryEntries(run: FunctionalTestRun): boolean {
  return Boolean(run.result_summary && Object.keys(run.result_summary).length > 0);
}

function formatSummaryValue(value: unknown): string {
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value);
  }
  return String(value ?? "");
}

let pollTimer: number | null = null;

function startOrUpdatePolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  const pending = Object.values(runs.value).filter((run) => ["prepared", "running"].includes(run.state));
  if (!pending.length) return;

  pollTimer = window.setInterval(() => {
    if (document.hidden) return;
    for (const run of pending) {
      void resources
        .testRun(run.id)
        .then((next) => {
          runs.value = { ...runs.value, [next.test_id]: next };
        })
        .catch(() => undefined);
    }
  }, 1_500);
}

watch(
  runs,
  () => {
    startOrUpdatePolling();
  },
  { deep: true },
);

onUnmounted(() => {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
});
</script>
