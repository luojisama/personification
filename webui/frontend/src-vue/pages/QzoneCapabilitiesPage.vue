<template>
  <div class="page-stack">
    <PageHeader
      :index="`QQ 空间 / ${sectionIndexTitle}`"
      title="QQ 空间"
      description="只读、登录恢复、外部写和结果核对分区处理；任何结果未知的写操作都不会自动重试。"
    />

    <!-- 能力矩阵 -->
    <template v-if="section === 'capabilities'">
      <QueryBoundary :pending="capabilitiesQuery.isPending.value" :error="capabilitiesQuery.error.value">
        <Panel
          v-if="capabilityRows.length"
          eyebrow="QZONE / CAPABILITY MATRIX"
          title="生产能力矩阵"
        >
          <div class="qzone-capability-grid">
            <article v-for="row in capabilityRows" :key="String(row.action)">
              <header>
                <strong>{{ ACTIONS[String(row.action)] || String(row.action) }}</strong>
                <StateBadge :tone="resolveCapabilityTone(String(row.state || 'unknown'))">
                  {{ String(row.state || 'unknown') }}
                </StateBadge>
              </header>
              <dl>
                <div>
                  <dt>接口</dt>
                  <dd><code>{{ formatValue(row.interface, '未观测') }}</code></dd>
                </div>
                <div>
                  <dt>HTTP / 业务码</dt>
                  <dd>{{ formatValue(row.http_status) }} / <code>{{ formatValue(row.business_code) }}</code></dd>
                </div>
                <div>
                  <dt>认证状态</dt>
                  <dd>{{ formatValue(row.auth_state, 'unknown') }}</dd>
                </div>
                <div>
                  <dt>诊断码</dt>
                  <dd><code>{{ formatValue(row.detail_code, 'unknown') }}</code></dd>
                </div>
                <div>
                  <dt>缺失字段</dt>
                  <dd>{{ Array.isArray(row.missing_fields) ? row.missing_fields.join('、') || '无' : '—' }}</dd>
                </div>
                <div>
                  <dt>最后验证</dt>
                  <dd>{{ formatDateTime(row.checked_at as string | number | null) }}</dd>
                </div>
              </dl>
            </article>
          </div>
        </Panel>
        <EmptyState v-else code="qzone_capabilities_empty">尚无 QQ 空间能力记录。</EmptyState>
      </QueryBoundary>
    </template>

    <!-- 登录与恢复 -->
    <template v-else-if="section === 'auth'">
      <QueryBoundary :pending="statusQuery.isPending.value" :error="statusQuery.error.value">
        <div v-if="statusQuery.data.value" class="summary-grid">
          <Panel eyebrow="AUTH / RUNTIME" title="登录与运行态">
            <dl class="compact-kv">
              <dt>总开关</dt>
              <dd>{{ statusQuery.data.value.enabled ? '启用' : '停用' }}</dd>
              <dt>Cookie</dt>
              <dd>{{ statusQuery.data.value.cookie_configured ? '已配置（不回传原值）' : '未配置' }}</dd>
              <dt>认证状态</dt>
              <dd>{{ formatValue(authRecord.state ?? authRecord.status, 'unknown') }}</dd>
              <dt>只读模式</dt>
              <dd>{{ statusQuery.data.value.read_only ? '是' : '否' }}</dd>
            </dl>
          </Panel>
          <Panel eyebrow="QUOTA / RECONCILIATION" title="额度与结果核对">
            <dl class="compact-kv">
              <dt>本月</dt>
              <dd>{{ formatValue(quotaRecord.used ?? quotaRecord.count) }} / {{ formatValue(quotaRecord.limit) }}</dd>
              <dt>下一可用</dt>
              <dd>{{ formatDateTime(statusQuery.data.value.next_eligible_at as number) }}</dd>
              <dt>对账状态</dt>
              <dd>{{ formatValue(reconciliationRecord.state, 'clear') }}</dd>
              <dt>阻塞写操作</dt>
              <dd>{{ reconciliationRecord.blocking ? '有，禁止重发' : '无' }}</dd>
            </dl>
          </Panel>
        </div>
      </QueryBoundary>

      <Panel eyebrow="AUTH / LOGIN RECOVERY" title="扫码恢复">
        <div class="inline-controls">
          <button
            class="button"
            type="button"
            :disabled="!selectedBotId || authMutation.isPending.value"
            @click="authMutation.mutate('start')"
          >
            创建扫码会话
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="!selectedBotId || authMutation.isPending.value"
            @click="authMutation.mutate('refresh')"
          >
            从协议端刷新 Cookie
          </button>
          <button
            v-if="sessionId"
            class="button button-danger"
            type="button"
            :disabled="authMutation.isPending.value"
            @click="authMutation.mutate('cancel')"
          >
            取消会话
          </button>
        </div>

        <div v-if="sessionId" class="qzone-login-session">
          <img
            :src="`${API_BASE}/qzone-management/auth/login/${encodeURIComponent(sessionId)}/qrcode`"
            alt="QZone 登录二维码"
            referrerpolicy="no-referrer"
          />
          <dl class="compact-kv">
            <dt>Session ID</dt>
            <dd><code>{{ sessionId }}</code></dd>
            <dt>状态</dt>
            <dd>{{ formatValue(loginStatusQuery.data.value?.status, '等待扫码') }}</dd>
            <dt>过期</dt>
            <dd>{{ formatDateTime(loginStatusQuery.data.value?.expires_at as number) }}</dd>
          </dl>
        </div>
      </Panel>

      <Panel eyebrow="AUTH / MANUAL COOKIE" title="手工安装 Cookie">
        <p class="muted-copy">
          秘密只在本次请求中提交，接口不会回显或写入审计详情。{{
            isSecureTransport ? '当前连接允许提交。' : '当前是远程 HTTP，前后端均会拒绝提交；请先部署 HTTPS。'
          }}
        </p>
        <textarea
          v-model="cookieInput"
          rows="4"
          autocomplete="off"
          spellcheck="false"
          placeholder="粘贴目标 Bot 的 QZone Cookie"
          :disabled="!isSecureTransport"
        />
        <button
          class="button"
          type="button"
          :disabled="!isSecureTransport || !selectedBotId || !cookieInput || authMutation.isPending.value"
          @click="submitCookie"
        >
          验证并安装
        </button>
      </Panel>

      <DiagnosticPanel
        v-for="(item, index) in diagnostics"
        :key="`${item.code}:${index}`"
        :diagnostic="item"
        :default-open="index === 0"
      />
    </template>

    <!-- 只读动态 -->
    <template v-else-if="section === 'feeds'">
      <QueryBoundary :pending="statusQuery.isPending.value" :error="statusQuery.error.value">
        <div v-if="statusQuery.data.value" class="summary-grid">
          <Panel eyebrow="AUTH / RUNTIME" title="登录与运行态">
            <dl class="compact-kv">
              <dt>总开关</dt>
              <dd>{{ statusQuery.data.value.enabled ? '启用' : '停用' }}</dd>
              <dt>Cookie</dt>
              <dd>{{ statusQuery.data.value.cookie_configured ? '已配置（不回传原值）' : '未配置' }}</dd>
              <dt>认证状态</dt>
              <dd>{{ formatValue(authRecord.state ?? authRecord.status, 'unknown') }}</dd>
              <dt>只读模式</dt>
              <dd>{{ statusQuery.data.value.read_only ? '是' : '否' }}</dd>
            </dl>
          </Panel>
          <Panel eyebrow="QUOTA / RECONCILIATION" title="额度与结果核对">
            <dl class="compact-kv">
              <dt>本月</dt>
              <dd>{{ formatValue(quotaRecord.used ?? quotaRecord.count) }} / {{ formatValue(quotaRecord.limit) }}</dd>
              <dt>下一可用</dt>
              <dd>{{ formatDateTime(statusQuery.data.value.next_eligible_at as number) }}</dd>
              <dt>对账状态</dt>
              <dd>{{ formatValue(reconciliationRecord.state, 'clear') }}</dd>
              <dt>阻塞写操作</dt>
              <dd>{{ reconciliationRecord.blocking ? '有，禁止重发' : '无' }}</dd>
            </dl>
          </Panel>
        </div>
      </QueryBoundary>

      <Panel eyebrow="EXTERNAL READ / CONFIRM" title="授权范围内读取">
        <p>下列操作会访问 QQ 空间公开或已授权数据并可能消耗网络请求，不会执行点赞、评论或发布。</p>
        <div class="inline-controls">
          <button
            class="button"
            type="button"
            :disabled="!selectedBotId || candidatesQuery.isFetching.value"
            @click="fetchCandidates"
          >
            读取本人动态
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="!selectedBotId || scanMutation.isPending.value"
            @click="triggerScan('social')"
          >
            扫描朋友动态
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="!selectedBotId || scanMutation.isPending.value"
            @click="triggerScan('inbound')"
          >
            轮询留言
          </button>
        </div>
      </Panel>

      <DiagnosticPanel
        v-if="candidatesQuery.isError.value"
        :diagnostic="diagnosticFromError(candidatesQuery.error.value)"
        default-open
      />
      <Panel
        v-else-if="candidateRows.length"
        eyebrow="READ / OWN FEEDS"
        :title="`本人动态候选（${candidateRows.length}）`"
      >
        <div class="trace-table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>Feed ID</th>
                <th>安全摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in candidateRows" :key="formatValue(item.feed_id)">
                <td>{{ formatDateTime(item.created_at as number) }}</td>
                <td><code>{{ formatValue(item.feed_id) }}</code></td>
                <td>{{ formatValue(item.content) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>
      <EmptyState v-else-if="candidatesQuery.isFetched.value" code="qzone_feeds_empty">
        本次读取没有返回可核对动态。
      </EmptyState>

      <DiagnosticPanel
        v-for="(item, index) in diagnostics"
        :key="`${item.code}:${index}`"
        :diagnostic="item"
        :default-open="index === 0"
      />
    </template>

    <!-- 写操作 -->
    <template v-else-if="section === 'operations'">
      <Panel eyebrow="EXTERNAL WRITE / HIGH RISK" title="发布一条 Agent 生成动态">
        <p>
          该操作会绕过额度、间隔和 Agent 参与决策，但仍计入月度额度。发布结果未知时 Operation ID 会保持隔离，页面不会自动重试。{{
            isSecureTransport ? '' : ' 当前是远程 HTTP，必须先部署 HTTPS。'
          }}
        </p>
        <dl class="compact-kv">
          <dt>目标 Bot</dt>
          <dd><code>{{ selectedBotId || '未选择' }}</code></dd>
          <dt>Operation ID</dt>
          <dd><code>{{ operationId }}</code></dd>
          <dt>动作</dt>
          <dd>生成并发布一条 QZone 说说</dd>
        </dl>
        <label>
          输入目标 Bot QQ 以确认
          <input v-model="confirmationInput" :disabled="!isSecureTransport" />
        </label>
        <button
          class="button button-danger"
          type="button"
          :disabled="!isSecureTransport || !selectedBotId || confirmationInput !== selectedBotId || publishMutation.isPending.value"
          @click="publishPost"
        >
          确认真实发布
        </button>
      </Panel>

      <Panel eyebrow="SUPPORTED ACTIONS" title="其他写操作">
        <p class="muted-copy">
          点赞、评论、子评论回复和转发必须从具体动态详情携带真实 feed、topic、父评论和目标 UIN；当前未选择完整目标，因此保持禁用，不构造猜测字段。
        </p>
        <div class="inline-controls">
          <button type="button" disabled>点赞</button>
          <button type="button" disabled>顶级评论</button>
          <button type="button" disabled>子评论回复</button>
          <button type="button" disabled>转发</button>
        </div>
      </Panel>

      <DiagnosticPanel
        v-for="(item, index) in diagnostics"
        :key="`${item.code}:${index}`"
        :diagnostic="item"
        :default-open="index === 0"
      />
    </template>

    <!-- 操作历史 -->
    <template v-else>
      <Panel eyebrow="OPERATIONS / UNKNOWN RESULTS" title="待人工核对">
        <div class="trace-table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>Operation ID</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>远端 ID</th>
                <th>安全摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in unresolvedOperations"
                :key="formatValue(item.operation_id)"
                @click="historyOperationId = formatValue(item.operation_id, '')"
              >
                <td><code>{{ formatValue(item.operation_id) }}</code></td>
                <td>
                  <StateBadge :tone="item.status === 'unknown' ? 'unknown' : 'warn'">
                    {{ formatValue(item.status) }}
                  </StateBadge>
                </td>
                <td>{{ formatDateTime(item.created_at as number) }}</td>
                <td>{{ formatValue(item.remote_id) }}</td>
                <td>{{ formatValue(item.content) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState
          v-if="!unresolvedOperations.length && !statusQuery.isPending.value"
          code="qzone_reconciliation_clear"
        >
          没有阻塞中的未知或发送中操作。
        </EmptyState>
      </Panel>

      <Panel eyebrow="VERIFY / SINGLE OPERATION" title="单个结果核对">
        <input v-model="historyOperationId" placeholder="Operation ID" />
        <dl v-if="singleOperationRecord" class="compact-kv">
          <dt>状态</dt>
          <dd>{{ formatValue(singleOperationRecord.status) }}</dd>
          <dt>结果码</dt>
          <dd><code>{{ formatValue(singleOperationRecord.result_code) }}</code></dd>
          <dt>远端 ID</dt>
          <dd>{{ formatValue(singleOperationRecord.remote_id) }}</dd>
        </dl>
        <div class="inline-controls">
          <button
            class="button"
            type="button"
            :disabled="!historyOperationId || !selectedBotId || historyMutation.isPending.value"
            @click="historyMutation.mutate('reconcile')"
          >
            从本人动态对账
          </button>
          <button
            class="button button-danger"
            type="button"
            :disabled="!historyOperationId || !selectedBotId || historyMutation.isPending.value"
            @click="resolveAbsent"
          >
            确认远端不存在
          </button>
        </div>
      </Panel>

      <DiagnosticPanel
        v-if="operationQuery.error.value"
        :diagnostic="diagnosticFromError(operationQuery.error.value)"
        default-open
      />
      <DiagnosticPanel
        v-for="(item, index) in diagnostics"
        :key="`${item.code}:${index}`"
        :diagnostic="item"
        :default-open="index === 0"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { useRoute } from "vue-router";

import { API_BASE } from "@/api/client";
import { diagnosticFromError, safeDiagnostic } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { OperationDiagnostic } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import DiagnosticPanel from "@vue-app/components/DiagnosticPanel.vue";
import EmptyState from "@vue-app/components/EmptyState.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import { useBotStore } from "@vue-app/stores/bot";

type JsonRecord = Record<string, unknown>;

const ACTIONS: Record<string, string> = {
  login_state: "登录态",
  own_feed_read: "读取自己的动态",
  friend_feed_read: "读取朋友动态",
  publish: "发布",
  like: "点赞",
  forward: "转发",
  top_level_comment: "顶级评论",
  child_comment_reply: "子评论回复",
};

const isSecureTransport =
  typeof window !== "undefined" &&
  (window.location.protocol === "https:" ||
    ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname));

function asRecord(val: unknown): JsonRecord {
  return val && typeof val === "object" && !Array.isArray(val) ? (val as JsonRecord) : {};
}

function asRecords(val: unknown): JsonRecord[] {
  return Array.isArray(val)
    ? val.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function formatValue(val: unknown, fallback = "—"): string {
  return val === null || val === undefined || val === "" ? fallback : String(val);
}

function createRandomOperationId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `qzone-${Date.now()}`;
}

function resolveCapabilityTone(state: string): "ok" | "warn" | "error" | "unknown" {
  if (state === "available") return "ok";
  if (state === "degraded") return "warn";
  if (state === "unavailable") return "error";
  return "unknown";
}

const route = useRoute();
const botStore = useBotStore();
const selectedBotId = computed(() => botStore.selectedBotId);

const section = computed(() => {
  const sec = String(route.params.section || "capabilities");
  return sec;
});

const sectionIndexTitle = computed(() => {
  switch (section.value) {
    case "capabilities":
      return "能力矩阵";
    case "auth":
      return "登录与恢复";
    case "feeds":
      return "只读动态";
    case "operations":
      return "写操作";
    default:
      return "操作历史";
  }
});

const diagnostics = ref<OperationDiagnostic[]>([]);
function recordDiagnostic(diag: OperationDiagnostic) {
  diagnostics.value = [diag, ...diagnostics.value].slice(0, 10);
}

// Capabilities Query
const capabilitiesQuery = useQuery({
  queryKey: computed(() => ["qzone-capabilities", selectedBotId.value]),
  queryFn: ({ signal }) => resources.qzoneCapabilities(signal, selectedBotId.value),
  enabled: computed(() => section.value === "capabilities"),
});
const capabilityRows = computed(() => asRecords(capabilitiesQuery.data.value?.items));

// Status Query (used in Auth, Feeds, History)
const statusQuery = useQuery({
  queryKey: computed(() => ["qzone-status", selectedBotId.value]),
  queryFn: ({ signal }) => resources.qzoneGet("status", {}, signal),
  enabled: computed(() => ["auth", "feeds", "history"].includes(section.value)),
});
const authRecord = computed(() => asRecord(statusQuery.data.value?.auth));
const quotaRecord = computed(() => asRecord(statusQuery.data.value?.quota));
const reconciliationRecord = computed(() => asRecord(statusQuery.data.value?.reconciliation));

// Auth state & mutation
const sessionId = ref("");
const cookieInput = ref("");
const loginStatusQuery = useQuery({
  queryKey: computed(() => ["qzone-login", sessionId.value]),
  queryFn: ({ signal }) =>
    resources.qzoneGet(`auth/login/${encodeURIComponent(sessionId.value)}/status`, {}, signal),
  enabled: computed(() => Boolean(sessionId.value)),
  refetchInterval: computed(() => (sessionId.value ? 3000 : false)),
});

const authMutation = useMutation({
  mutationFn: async (action: "start" | "cancel" | "refresh" | "cookie") => {
    if (action === "start") {
      return resources.qzonePost("auth/login/start", { bot_id: selectedBotId.value });
    }
    if (action === "cancel") {
      return resources.qzonePost(`auth/login/${encodeURIComponent(sessionId.value)}/cancel`);
    }
    if (action === "refresh") {
      return resources.qzonePost("refresh-cookie", { bot_id: selectedBotId.value });
    }
    return resources.qzonePost("auth/cookie", { bot_id: selectedBotId.value, cookie: cookieInput.value });
  },
  onSuccess: (result, action) => {
    const diag = asRecord(result.diagnostic);
    recordDiagnostic(safeDiagnostic(diag));
    if (action === "cancel") {
      sessionId.value = "";
    } else {
      const nextSession = String(result.session_id ?? "");
      if (nextSession) sessionId.value = nextSession;
    }
    cookieInput.value = "";
    void statusQuery.refetch();
  },
  onError: (error) => {
    recordDiagnostic(diagnosticFromError(error));
  },
});

function submitCookie() {
  if (window.confirm(`确认将 Cookie 安装到 Bot ${selectedBotId.value}？`)) {
    authMutation.mutate("cookie");
  }
}

// Feeds state & mutations
const candidatesQuery = useQuery({
  queryKey: computed(() => ["qzone-candidates", selectedBotId.value]),
  queryFn: ({ signal }) => resources.qzoneGet("reconcile-candidates", { bot_id: selectedBotId.value }, signal),
  enabled: false,
});
const candidateRows = computed(() => asRecords(candidatesQuery.data.value?.candidates));

const scanMutation = useMutation({
  mutationFn: (kind: "social" | "inbound") =>
    resources.qzonePost("scan-now", { kind, bot_id: selectedBotId.value }),
  onSuccess: (result) => {
    recordDiagnostic(safeDiagnostic(asRecord(result.diagnostic)));
    void statusQuery.refetch();
  },
  onError: (error) => {
    recordDiagnostic(diagnosticFromError(error));
  },
});

function fetchCandidates() {
  if (window.confirm(`确认读取 Bot ${selectedBotId.value} 的本人动态，用于生成对账候选？`)) {
    void candidatesQuery.refetch();
  }
}

function triggerScan(kind: "social" | "inbound") {
  const label = kind === "social" ? "有限好友动态扫描" : "留言轮询";
  if (window.confirm(`确认执行 Bot ${selectedBotId.value} 的${label}？`)) {
    scanMutation.mutate(kind);
  }
}

// Operations state & mutation
const confirmationInput = ref("");
const operationId = ref(createRandomOperationId());

const publishMutation = useMutation({
  mutationFn: () =>
    resources.qzonePost("post-now", {
      bot_id: selectedBotId.value,
      operation_id: operationId.value,
    }),
  onSuccess: (result) => {
    recordDiagnostic(safeDiagnostic(asRecord(result.diagnostic)));
    operationId.value = createRandomOperationId();
    confirmationInput.value = "";
  },
  onError: (error) => {
    recordDiagnostic(diagnosticFromError(error));
  },
});

function publishPost() {
  if (window.confirm(`最后确认：使用 Bot ${selectedBotId.value} 发布一条真实 QZone 动态？`)) {
    publishMutation.mutate();
  }
}

// History state & mutations
const historyOperationId = ref("");
const unresolvedOperations = computed(() => asRecords(asRecord(statusQuery.data.value?.reconciliation).operations));

const operationQuery = useQuery({
  queryKey: computed(() => ["qzone-operation", historyOperationId.value]),
  queryFn: ({ signal }) =>
    resources.qzoneGet(`operations/${encodeURIComponent(historyOperationId.value)}`, {}, signal),
  enabled: computed(() => Boolean(historyOperationId.value)),
});
const singleOperationRecord = computed(() =>
  operationQuery.data.value ? asRecord(operationQuery.data.value.operation) : null,
);

const historyMutation = useMutation({
  mutationFn: (action: "reconcile" | "absent") =>
    resources.qzonePost(
      `operations/${encodeURIComponent(historyOperationId.value)}/${
        action === "reconcile" ? "reconcile" : "resolve-absent"
      }`,
      { bot_id: selectedBotId.value },
    ),
  onSuccess: (result) => {
    recordDiagnostic(safeDiagnostic(asRecord(result.diagnostic)));
    void statusQuery.refetch();
    void operationQuery.refetch();
  },
  onError: (error) => {
    recordDiagnostic(diagnosticFromError(error));
  },
});

function resolveAbsent() {
  if (
    window.confirm(
      `仅当你已人工确认远端不存在 Operation ${historyOperationId.value} 对应动态时继续。确认？`,
    )
  ) {
    historyMutation.mutate("absent");
  }
}
</script>
