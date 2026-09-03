<template>
  <div class="page-stack">
    <PageHeader
      index="04"
      title="功能体检"
      description="18 类体检按本地只读、外部读取、外部写入分级。模型与媒体探针会明确确认；公网 HTTP 下外部写测试会被服务端拒绝。"
    />

    <!-- 结构化诊断告警面板 -->
    <section v-if="activeDiagnostic" class="panel diagnostic-alert-panel" role="region" aria-label="诊断详情">
      <header class="panel-heading">
        <div class="panel-heading-copy">
          <div class="panel-eyebrow">DIAGNOSTIC ERROR</div>
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
          <div class="health-test-grid">
            <article
              v-for="test in catalogQuery.data.value.tests"
              :key="test.id"
              class="health-test-card"
            >
              <header class="test-card-header">
                <div>
                  <strong>{{ test.label }}</strong>
                  <small class="test-category">{{ test.category }}</small>
                </div>
                <StateBadge
                  :tone="test.risk === 'external_write' ? 'warn' : test.risk === 'external_read' ? 'warn' : 'ok'"
                >
                  {{ RISK_LABELS[test.risk] }}
                </StateBadge>
              </header>

              <div v-if="test.risk === 'external_write'" class="test-target-field">
                <label :for="'target-' + test.id">目标复核摘要</label>
                <input
                  :id="'target-' + test.id"
                  v-model="targets[test.id]"
                  type="text"
                  :placeholder="'Bot、目标 QQ/群或动态 ID'"
                  :aria-invalid="false"
                  :aria-describedby="'hint-' + test.id"
                />
                <small :id="'hint-' + test.id" class="target-field-hint">
                  公网 HTTP 模式下外部写操作受服务端强制防护
                </small>
              </div>

              <div class="test-card-actions">
                <button
                  type="button"
                  class="button button-secondary"
                  :disabled="isTestBusy(test.id)"
                  @click="handleRunRequest(test)"
                >
                  {{ isTestBusy(test.id) ? "运行中…" : test.risk === "local_read" ? "运行本地检查" : "准备并确认" }}
                </button>
              </div>

              <!-- 结构化测试运行结果 -->
              <template v-for="run in [runs[test.id]]" :key="`run:${test.id}`">
                <div v-if="run" class="test-run-result">
                  <div class="result-status-row">
                    <StateBadge :tone="getRunTone(run.state)" :raw="run.state">
                      {{ run.state }}
                    </StateBadge>
                    <code class="diagnostic-code-pill">{{ run.diagnostic_code }}</code>
                    <span class="result-time-meta">
                      {{ formatDuration(run.duration_ms) }} · {{ formatDateTime(run.finished_at) }}
                    </span>
                  </div>
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

const RISK_LABELS = {
  local_read: "本地只读",
  external_read: "外部读取",
  external_write: "外部写入",
} as const;

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

function getRunTone(state: FunctionalTestRun["state"]): "ok" | "warn" | "error" | "running" | "unknown" {
  if (state === "succeeded") return "ok";
  if (state === "failed") return "error";
  if (state === "prepared" || state === "running") return "running";
  if (state === "unknown") return "warn";
  return "unknown";
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
