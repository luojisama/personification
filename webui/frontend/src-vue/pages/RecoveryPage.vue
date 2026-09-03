<template>
  <div class="page-stack">
    <PageHeader
      index="09"
      title="恢复队列"
      description="队列保存失败的入站消息摘要，并使用当前上下文重新生成。发送结果未知或部分送达时只进入人工核对区，绝不自动重放。"
    >
      <template #actions>
        <label class="select-field">
          <span>状态</span>
          <select :value="status" @change="onStatusChange">
            <option v-for="filter in FILTERS" :key="filter.value" :value="filter.value">
              {{ filter.label }}
            </option>
          </select>
        </label>
      </template>
    </PageHeader>

    <div v-if="diagnostics.length > 0" class="diagnostic-stack" aria-live="polite">
      <article
        v-for="(diag, idx) in diagnostics"
        :key="diag.operation_id || diag.trace_id || `${diag.code}:${idx}`"
        class="security-manifest"
      >
        <strong>[{{ diag.code }}] {{ diag.title }}</strong>
        <p>{{ diag.message }}</p>
        <small v-if="diag.suggestion">建议：{{ diag.suggestion }}</small>
      </article>
    </div>

    <QueryBoundary :pending="isPending" :error="error" :empty="!isPending && (!data || data.items.length === 0)" empty-text="当前筛选条件下没有恢复项。">
      <div v-if="data && data.items.length > 0" class="recovery-list">
        <Panel
          v-for="item in data.items"
          :key="item.id"
          as="article"
          class="recovery-dossier"
          :eyebrow="`RECOVERY / ${String(item.id).padStart(6, '0')}`"
          :title="item.safe_summary || '没有可展示的入站摘要'"
        >
          <div class="recovery-status-line">
            <StateBadge :tone="recoveryTone(item.status)" :raw="item.status">
              {{ recoveryStatusLabel(item.status) }}
            </StateBadge>
            <span>{{ sessionTypeLabel(item.session_type) }} · {{ item.session_id }}</span>
            <span>尝试 {{ item.attempts }} / 3 次</span>
          </div>

          <dl class="recovery-evidence-grid">
            <div><dt>失败分类</dt><dd><code>{{ item.failure_class }}</code></dd></div>
            <div><dt>失败阶段</dt><dd><code>{{ item.failure_stage }}</code></dd></div>
            <div><dt>首次失败</dt><dd>{{ formatDateTime(item.first_failed_at) }}</dd></div>
            <div><dt>过期时间</dt><dd>{{ formatDateTime(item.expires_at) }}</dd></div>
            <div><dt>原消息 ID</dt><dd><code>{{ shortId(item.message_id) }}</code></dd></div>
            <div><dt>Trace ID</dt><dd><code>{{ shortId(item.trace_id) }}</code></dd></div>
          </dl>

          <div v-if="item.outcome_unknown" class="unknown-warning" role="alert">
            发送结果未知：已禁止自动恢复。只有确认未发送后才可重新开放。
          </div>
          <div v-if="item.missing_segments && item.missing_segments.length > 0" class="unknown-warning" role="alert">
            部分发送缺失分段：{{ item.missing_segments.join("、") }}。禁止整批重放。
          </div>

          <footer v-if="canAbandon(item) || canConfirmRetry(item)" class="dossier-actions">
            <button
              v-if="canConfirmRetry(item)"
              class="button button-primary"
              type="button"
              :disabled="actionPendingId === item.id"
              @click="handleRetry(item.id)"
            >
              确认未发送并重试
            </button>
            <button
              v-if="canAbandon(item)"
              class="button button-danger"
              type="button"
              :disabled="actionPendingId === item.id"
              @click="handleAbandon(item.id)"
            >
              放弃此恢复项
            </button>
          </footer>
        </Panel>
      </div>
    </QueryBoundary>

    <nav v-if="data && data.total_pages > 1" class="pagination" aria-label="分页导航">
      <button type="button" :disabled="page <= 1" @click="setPage(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ data.total_pages }} 页 (共 {{ data.total }} 项)</span>
      <button type="button" :disabled="page >= data.total_pages" @click="setPage(page + 1)">下一页</button>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import { diagnosticFromError } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { OperationDiagnostic, RecoveryItem, RecoveryStatus } from "@/api/types";
import { formatDateTime, shortId } from "@/lib/format";
import { recoveryStatusLabel, sessionTypeLabel } from "@/lib/labels";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";

const FILTERS = [
  { value: "", label: "全部" },
  { value: "pending", label: "待恢复" },
  { value: "processing", label: "处理中" },
  { value: "quarantined", label: "人工核对区" },
  { value: "recovered", label: "已恢复" },
  { value: "expired", label: "已过期" },
];

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();

const page = ref(Math.max(1, Number(route.query.page) || 1));
const status = ref<string>(
  typeof route.params.section === "string" && route.params.section !== "all" && route.params.section !== "queue"
    ? route.params.section
    : "",
);
const diagnostics = ref<OperationDiagnostic[]>([]);
const actionPendingId = ref<number | null>(null);

const recoveryQueryKey = computed(() => ["recovery", page.value, status.value]);
const { data, isPending, error } = useQuery({
  queryKey: recoveryQueryKey,
  queryFn: ({ signal }) => resources.recovery(page.value, 20, status.value, signal),
});

function recoveryTone(itemStatus: RecoveryStatus): "ok" | "warn" | "error" | "running" | "unknown" {
  if (itemStatus === "recovered") return "ok";
  if (itemStatus === "processing") return "running";
  if (itemStatus === "quarantined") return "unknown";
  if (itemStatus === "expired" || itemStatus === "exhausted") return "error";
  if (itemStatus === "pending") return "warn";
  return "unknown";
}

function canAbandon(item: RecoveryItem): boolean {
  return item.status === "pending" || item.status === "quarantined";
}

function canConfirmRetry(item: RecoveryItem): boolean {
  return item.status === "quarantined" && item.failure_class === "delivery_unknown";
}

function recordDiagnostic(diag: OperationDiagnostic) {
  diagnostics.value.unshift(diag);
}

const abandonMutation = useMutation({
  mutationFn: (id: number) => resources.abandonRecovery(id),
  onSuccess: (diag) => {
    recordDiagnostic(diag);
    void queryClient.invalidateQueries({ queryKey: ["recovery"] });
  },
  onError: (err) => recordDiagnostic(diagnosticFromError(err)),
  onSettled: () => { actionPendingId.value = null; },
});

const retryMutation = useMutation({
  mutationFn: (id: number) => resources.retryRecovery(id),
  onSuccess: (diag) => {
    recordDiagnostic(diag);
    void queryClient.invalidateQueries({ queryKey: ["recovery"] });
  },
  onError: (err) => recordDiagnostic(diagnosticFromError(err)),
  onSettled: () => { actionPendingId.value = null; },
});

function handleAbandon(id: number) {
  if (!window.confirm("请确认是否放弃此条恢复项？放弃后将不会再次重试。")) return;
  actionPendingId.value = id;
  abandonMutation.mutate(id);
}

function handleRetry(id: number) {
  const confirmed = window.confirm("请确认你已在 QQ 或外部系统核对：这条消息明确没有送达。确认后系统才会重新生成，不会重放旧回复。是否继续？");
  if (!confirmed) return;
  actionPendingId.value = id;
  retryMutation.mutate(id);
}

function setPage(newPage: number) {
  page.value = newPage;
  void router.replace({ query: { ...route.query, page: newPage > 1 ? newPage : undefined } });
}

function onStatusChange(event: Event) {
  const target = event.target as HTMLSelectElement;
  status.value = target.value;
  page.value = 1;
  void router.replace({
    name: "runtime-recovery",
    params: { ...route.params, section: target.value || "queue" },
    query: { ...route.query, page: undefined },
  });
}

watch(
  () => route.params.section,
  (nextSection) => {
    const next = typeof nextSection === "string" && nextSection !== "all" && nextSection !== "queue" ? nextSection : "";
    if (next !== status.value) {
      status.value = next;
      page.value = 1;
    }
  },
);
</script>
