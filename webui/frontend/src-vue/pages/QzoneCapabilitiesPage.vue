<template>
  <div class="page-stack qzone-page">
    <PageHeader
      index="QQ 空间"
      :title="sectionCopy.title"
      :description="sectionCopy.description"
    >
      <template #actions>
        <StateBadge :tone="selectedBotId ? 'ok' : 'warn'">
          {{ selectedBotId ? `当前 Bot · ${selectedBotId}` : '未选择 QQ Bot' }}
        </StateBadge>
      </template>
    </PageHeader>
    <p v-if="!selectedBotId" class="qzone-notice">连接 QQ Bot 后，使用侧栏选择要操作的账号。</p>

    <!-- 能力矩阵 -->
    <template v-if="section === 'capabilities'">
      <nav class="qzone-quick-links" aria-label="QQ 空间常用操作">
        <RouterLink v-for="item in quickLinks" :key="item.to" :to="item.to">
          <Icon :name="item.icon" />
          <span><strong>{{ item.label }}</strong><small>{{ item.description }}</small></span>
          <Icon name="chevron" />
        </RouterLink>
      </nav>
      <QueryBoundary :pending="capabilitiesQuery.isPending.value" :error="capabilitiesQuery.error.value">
        <Panel
          v-if="capabilityRows.length"
          eyebrow="QZONE / CAPABILITY MATRIX"
          title="空间功能状态"
        >
          <div class="qzone-capability-grid">
            <article v-for="row in capabilityRows" :key="String(row.action)">
              <header>
                <strong>{{ ACTIONS[String(row.action)] || String(row.action) }}</strong>
                <StateBadge :tone="resolveCapabilityTone(String(row.state || 'unknown'))" :raw="formatValue(row.state, 'unknown')">
                  {{ statusLabel(row.state) }}
                </StateBadge>
              </header>
              <details class="qzone-technical-details">
                <summary>查看检测详情</summary>
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
                  <dd>{{ statusLabel(row.auth_state) }}</dd>
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
              </details>
            </article>
          </div>
        </Panel>
        <EmptyState v-else code="qzone_capabilities_empty">尚无 QQ 空间能力记录。</EmptyState>
      </QueryBoundary>
    </template>

    <!-- 登录与恢复 -->
    <template v-else-if="section === 'auth'">
      <Panel eyebrow="AUTH / LOGIN RECOVERY" title="登录或更新登录状态">
        <p>用当前 Bot 对应的手机 QQ 扫码，或从已连接的 QQ 协议端获取登录信息。</p>
        <div class="inline-controls">
          <button
            class="button button-primary"
            type="button"
            :disabled="!selectedBotId || authMutation.isPending.value"
            @click="authMutation.mutate('start')"
          >
            {{ sessionId ? '重新生成二维码' : '生成登录二维码' }}
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="!selectedBotId || authMutation.isPending.value"
            @click="authMutation.mutate('refresh')"
          >
            从协议端获取登录信息
          </button>
          <button
            v-if="sessionId && !loginStatusQuery.data.value?.terminal"
            class="button button-danger"
            type="button"
            :disabled="authMutation.isPending.value"
            @click="authMutation.mutate('cancel')"
          >
            取消扫码
          </button>
        </div>

        <div v-if="sessionId" class="qzone-login-session">
          <img
            v-if="loginStatusQuery.data.value?.qr_ready && !loginStatusQuery.data.value?.terminal && !loginStatusQuery.isError.value"
            :src="`${API_BASE}/qzone-management/auth/login/${encodeURIComponent(sessionId)}/qrcode`"
            alt="QQ 空间登录二维码"
            referrerpolicy="no-referrer"
          />
          <div role="status">
            <strong>{{ loginStatusQuery.isError.value ? '无法读取扫码状态，请重新生成二维码' : formatValue(loginStatusQuery.data.value?.message, '请用手机 QQ 扫码') }}</strong>
            <p v-if="loginStatusQuery.data.value?.expires_at && !loginStatusQuery.data.value?.terminal">有效至 {{ formatDateTime(loginStatusQuery.data.value?.expires_at as number) }}</p>
            <RouterLink v-if="loginStatusQuery.data.value?.status === 'success'" class="text-link" to="/runtime/qzone/feeds">检查动态读取 →</RouterLink>
          </div>
        </div>
      </Panel>

      <QueryBoundary :pending="statusQuery.isPending.value" :error="statusQuery.error.value">
        <div v-if="statusQuery.data.value" class="summary-grid">
          <Panel eyebrow="AUTH / RUNTIME" title="当前登录状态">
            <dl class="compact-kv">
              <div><dt>总开关</dt><dd>{{ statusQuery.data.value.enabled ? '启用' : '停用' }}</dd></div>
              <div><dt>Cookie</dt><dd>{{ statusQuery.data.value.cookie_configured ? '已配置' : '未配置' }}</dd></div>
              <div><dt>登录来源</dt><dd>{{ credentialLabel(statusQuery.data.value.credential_source) }}</dd></div>
              <div><dt>身份匹配</dt><dd>{{ identityVerificationText }}</dd></div>
              <div><dt>认证状态</dt><dd>{{ statusLabel(authRecord.state ?? authRecord.status) }}</dd></div>
              <div><dt>只读模式</dt><dd>{{ statusQuery.data.value.read_only ? '是' : '否' }}</dd></div>
            </dl>
          </Panel>
          <Panel eyebrow="QUOTA / RECONCILIATION" title="发布额度与状态">
            <dl class="compact-kv">
              <div><dt>本月</dt><dd>{{ formatValue(quotaRecord.used ?? quotaRecord.count) }} / {{ formatValue(quotaRecord.limit) }}</dd></div>
              <div><dt>下次自动发布可用时间</dt><dd>{{ formatDateTime(statusQuery.data.value.next_eligible_at as number) }}</dd></div>
              <div><dt>对账状态</dt><dd>{{ statusLabel(reconciliationRecord.state ?? 'clear') }}</dd></div>
              <div><dt>待核对发布</dt><dd>{{ reconciliationRecord.blocking ? '有' : '无' }}</dd></div>
            </dl>
          </Panel>
        </div>
      </QueryBoundary>

      <Panel v-if="botCredentialRows.length" eyebrow="AUTH / BOT ISOLATION" title="各 Bot 的登录与功能状态">
        <div class="trace-table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>Bot</th>
                <th>登录信息</th>
                <th>来源</th>
                <th>身份</th>
                <th>从协议端获取登录</th>
                <th>读取</th>
                <th>发布</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in botCredentialRows" :key="row.botId">
                <td><code>{{ row.botId }}</code></td>
                <td>{{ row.configured ? '已配置' : '未配置' }}</td>
                <td>{{ credentialLabel(row.source) }}</td>
                <td>{{ row.identity }}</td>
                <td>{{ statusLabel(row.cookieExport) }}</td>
                <td>{{ statusLabel(row.webRead) }}</td>
                <td>{{ statusLabel(row.webWrite) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted-copy">
          在侧栏切换 Bot 后，登录、读取和发布会使用新选中的账号。
        </p>
      </Panel>

      <Panel v-if="canImportCookie" eyebrow="AUTH / MANUAL COOKIE" title="手动导入 Cookie">
          <TextareaField
            v-model="cookieInput"
            label="所选 Bot 的 QZone Cookie"
            id="qzone-cookie-input"
            description="导入前会核对 Cookie 对应的 QQ 是否与当前 Bot 一致。"
            :rows="4"
            autocomplete="off"
            :spellcheck="false"
            placeholder="仅在必要时粘贴所选 Bot 的 QZone Cookie"
          />
          <button
            class="button"
            type="button"
            :disabled="!selectedBotId || !cookieInput || authMutation.isPending.value"
            @click="submitCookie"
          >
            验证并导入
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
      <Panel eyebrow="EXTERNAL READ / CONFIRM" title="测试动态读取">
        <p>检查当前 Bot 是否能登录 QQ 空间并读取动态。也可以直接查看它最近发过的动态。</p>
        <TextField
          v-model="readOnlyTargetUserId"
          label="好友 QQ（选填）"
          inputmode="numeric"
          autocomplete="off"
          description="填写后，读取检查还会尝试读取该好友的动态。"
          placeholder="留空时检查当前 Bot 自己的动态"
        />
        <div class="inline-controls">
          <button
            class="button button-primary"
            type="button"
            :disabled="!selectedBotId || readOnlyMutation.isPending.value"
            @click="runReadOnlyDiagnostics"
          >
            {{ readOnlyMutation.isPending.value ? '正在检查…' : '开始读取检查' }}
          </button>
          <button
            class="button button-secondary"
            type="button"
            :disabled="!selectedBotId || candidatesQuery.isFetching.value"
            @click="candidatesQuery.refetch()"
          >
            {{ candidatesQuery.isFetching.value ? '正在读取…' : '查看自己的动态' }}
          </button>
        </div>
      </Panel>

      <Panel v-if="readOnlyStages.length" eyebrow="READ / LAST DIAGNOSTIC" title="读取检查结果">
        <div class="trace-table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>阶段</th>
                <th>状态</th>
                <th>结果代码</th>
                <th>耗时</th>
                <th>数量</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in readOnlyStages" :key="item.key">
                <td :title="item.key">{{ item.label }}</td>
                <td :title="item.status">{{ statusLabel(item.status) }}</td>
                <td><code>{{ item.code }}</code></td>
                <td>{{ item.elapsed_ms }} ms</td>
                <td>{{ formatValue(item.count, '—') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="lastReadOnlySuggestion" class="muted-copy">建议：{{ lastReadOnlySuggestion }}</p>
      </Panel>

      <DiagnosticPanel
        v-if="candidatesQuery.isError.value"
        :diagnostic="qzoneError(candidatesQuery.error.value)"
        default-open
      />
      <Panel
        v-else-if="candidateRows.length"
        eyebrow="READ / OWN FEEDS"
        :title="`自己的动态（${candidateRows.length} 条）`"
      >
        <div class="trace-table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>动态 ID</th>
                <th>内容摘要</th>
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
        没有读到近期动态。
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
      <Panel eyebrow="EXTERNAL WRITE / HIGH RISK" title="生成并发布动态">
        <p>让 Bot 生成一条说说并立即发布到自己的 QQ 空间。手动发布计入本月额度。</p>
        <p v-if="uncertainPublish" class="qzone-notice" role="status">
          这次发布的结果还不确定。请先到<RouterLink class="text-link" :to="{ path: '/runtime/qzone/history', query: { operation: uncertainPublish } }">操作记录</RouterLink>核对。
        </p>
        <div class="inline-controls">
        <button
          class="button button-danger"
          type="button"
          :disabled="!selectedBotId || !!uncertainPublish || publishMutation.isPending.value"
          @click="publishTarget = selectedBotId"
        >
          {{ publishMutation.isPending.value ? '正在生成并发布…' : '生成并发布' }}
        </button>
        <RouterLink class="button button-secondary" to="/runtime/qzone/auth">检查登录</RouterLink>
        </div>
      </Panel>
      <ConfirmDialog
        :open="!!publishTarget"
        title="发布到 QQ 空间？"
        :description="`Bot ${publishTarget} 将生成一条说说并立即发布。只想验证登录时，可以使用读取检查。`"
        confirm-label="确认发布"
        dangerous
        @update:open="publishTarget = ''"
        @confirm="publishPost"
      />

      <Panel eyebrow="SUPPORTED ACTIONS" title="运行互动任务">
        <p>按当前 Bot 的互动规则运行一次。检查好友动态可能触发点赞、评论或转发，检查留言可能触发回复。</p>
        <div class="inline-controls">
          <button class="button button-secondary" type="button" :disabled="!selectedBotId || scanMutation.isPending.value" @click="triggerScan('social')">检查好友动态</button>
          <button class="button button-secondary" type="button" :disabled="!selectedBotId || scanMutation.isPending.value" @click="triggerScan('inbound')">检查新留言</button>
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
                <th>操作编号</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>动态 ID</th>
                <th>内容摘要</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in unresolvedOperations"
                :key="formatValue(item.operation_id)"
              >
                <td><button class="text-button" type="button" @click="historyOperationId = formatValue(item.operation_id, '')"><code>{{ formatValue(item.operation_id) }}</code></button></td>
                <td>
                  <StateBadge :tone="item.status === 'unknown' ? 'unknown' : 'warn'">
                    {{ statusLabel(item.status) }}
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
          暂无需要核对的发布。
        </EmptyState>
      </Panel>

      <Panel eyebrow="VERIFY / SINGLE OPERATION" title="查询发布结果">
        <TextField v-model="historyOperationId" label="操作编号" placeholder="从上方记录选择，或粘贴操作编号" />
        <dl v-if="singleOperationRecord" class="compact-kv">
          <div><dt>状态</dt><dd>{{ statusLabel(singleOperationRecord.status) }}</dd></div>
          <div><dt>结果码</dt><dd><code>{{ formatValue(singleOperationRecord.result_code) }}</code></dd></div>
          <div><dt>动态 ID</dt><dd>{{ formatValue(singleOperationRecord.remote_id) }}</dd></div>
        </dl>
        <div class="inline-controls">
          <button
            class="button"
            type="button"
            :disabled="!historyOperationId || !selectedBotId || historyMutation.isPending.value"
            @click="historyMutation.mutate('reconcile')"
          >
            核对已发布的动态
          </button>
          <button
            class="button button-danger"
            type="button"
            :disabled="!historyOperationId || !selectedBotId || historyMutation.isPending.value"
            @click="resolveAbsent"
          >
            标记为未发布
          </button>
        </div>
      </Panel>

      <DiagnosticPanel
        v-if="operationQuery.error.value"
        :diagnostic="qzoneError(operationQuery.error.value)"
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
import { computed, ref, watch } from "vue";
import { useMutation, useQuery } from "@tanstack/vue-query";
import { RouterLink, useRoute } from "vue-router";

import { API_BASE, ApiError, api } from "@/api/client";
import { diagnosticFromError, safeDiagnostic } from "@/api/diagnostics";
import { resources } from "@/api/resources";
import type { OperationDiagnostic } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import ConfirmDialog from "@vue-app/components/ConfirmDialog.vue";
import DiagnosticPanel from "@vue-app/components/DiagnosticPanel.vue";
import EmptyState from "@vue-app/components/EmptyState.vue";
import Icon from "@vue-app/components/Icon.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextareaField from "@vue-app/components/forms/TextareaField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { useBotStore } from "@vue-app/stores/bot";

type JsonRecord = Record<string, unknown>;

type BotCredentialRow = {
  botId: string;
  configured: boolean;
  source: string;
  identity: string;
  cookieExport: string;
  webRead: string;
  webWrite: string;
};

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

const canImportCookie =
  typeof window !== "undefined" &&
  (window.location.protocol === "https:" ||
    ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname));

const sectionInfo = {
  capabilities: { title: "QQ 空间", description: "登录 Bot 的 QQ 空间，检查能否读取动态，或手动发布一条说说。" },
  auth: { title: "QQ 空间登录", description: "扫码登录，或从当前 Bot 的 QQ 协议端获取登录信息。" },
  feeds: { title: "读取 QQ 空间动态", description: "查看 Bot 最近发布的动态，或检查它能否读取好友动态。" },
  operations: { title: "发布与互动", description: "手动发布说说，或按 Bot 的设置运行一次互动任务。" },
  history: { title: "QQ 空间操作记录", description: "查看需要核对的发布，并按操作编号查询结果。" },
};

const quickLinks = [
  { to: "/runtime/qzone/auth", label: "登录 QQ 空间", description: "扫码或使用现有登录", icon: "shield" },
  { to: "/runtime/qzone/feeds", label: "检查动态读取", description: "检查自己的动态，也可指定好友", icon: "search" },
  { to: "/runtime/qzone/operations", label: "发布与互动", description: "手动发布说说或运行互动任务", icon: "signal" },
] as const;

const statusLabels: Record<string, string> = {
  available: "可用", unavailable: "不可用", degraded: "部分可用", unknown: "待确认",
  healthy: "已登录", authenticated: "已登录", login_required: "需要登录", risk_blocked: "QQ 限制访问",
  refresh_failed: "更新登录失败", clear: "无需核对", in_progress: "处理中",
  reserved: "等待发布", dispatching: "正在发布", succeeded: "已发布",
  definite_failure: "未发布", ok: "通过", error: "未通过", skipped: "已跳过",
};

function statusLabel(value: unknown): string {
  const status = formatValue(value, "unknown");
  return statusLabels[status] ?? `状态待确认（${status}）`;
}

const credentialLabels: Record<string, string> = {
  onebot: "QQ 协议端", manual: "手动导入", ptlogin: "手机 QQ 扫码",
  read_only_diagnostics: "读取检查", legacy_config: "旧配置迁移",
};

function credentialLabel(value: unknown): string {
  const source = formatValue(value, "");
  return source ? (credentialLabels[source] ?? `其他来源（${source}）`) : "未配置";
}

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
  const random = globalThis.crypto.getRandomValues(new Uint32Array(4));
  return `qzone-${Array.from(random, (value) => value.toString(16).padStart(8, "0")).join("")}`;
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

const sectionCopy = computed(() => sectionInfo[section.value as keyof typeof sectionInfo] ?? sectionInfo.history);

const diagnostics = ref<OperationDiagnostic[]>([]);
function recordDiagnostic(diag: OperationDiagnostic) {
  diagnostics.value = [diag, ...diagnostics.value].slice(0, 10);
}

function qzoneError(error: unknown): OperationDiagnostic {
  if (error instanceof ApiError) {
    const detail = asRecord(asRecord(error.detail).detail);
    if (error.code.startsWith("qzone_") && typeof detail.title === "string" && typeof detail.message === "string") {
      return safeDiagnostic(detail);
    }
  }
  return diagnosticFromError(error);
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
  queryFn: ({ signal }) => resources.qzoneGet("status", { bot_id: selectedBotId.value }, signal),
  enabled: computed(() => ["auth", "history"].includes(section.value)),
});
const authRecord = computed(() => asRecord(statusQuery.data.value?.auth));
const quotaRecord = computed(() => asRecord(statusQuery.data.value?.quota));
const reconciliationRecord = computed(() => asRecord(statusQuery.data.value?.reconciliation));
const identityVerificationText = computed(() => {
  if (authRecord.value.credential_configured !== true) return "未配置";
  return authRecord.value.credential_identity_verification === "verified" ? "安装时已验证" : "未记录，需要只读诊断确认"
});

function capabilityState(capabilities: JsonRecord, name: string): string {
  return formatValue(asRecord(capabilities[name]).state, "unknown");
}

const botCredentialRows = computed<BotCredentialRow[]>(() => {
  const authByBot = asRecord(statusQuery.data.value?.auth_by_bot);
  const capabilitiesByBot = asRecord(statusQuery.data.value?.capabilities_by_bot);
  return Object.entries(authByBot)
    .filter(([botId]) => Boolean(botId))
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([botId, rawAuth]) => {
      const auth = asRecord(rawAuth);
      const capabilities = asRecord(capabilitiesByBot[botId]);
      const configured = auth.credential_configured === true;
      return {
        botId,
        configured,
        source: formatValue(auth.credential_source, ""),
        identity: !configured
          ? "未配置"
          : auth.credential_identity_verification === "verified"
            ? "安装时已验证"
            : "未记录",
        cookieExport: capabilityState(capabilities, "qzone.cookie_export"),
        webRead: capabilityState(capabilities, "qzone.web_read"),
        webWrite: capabilityState(capabilities, "qzone.web_write"),
      };
    });
});

// Auth state & mutation
const sessionId = ref("");
const cookieInput = ref("");
const loginStatusQuery = useQuery({
  queryKey: computed(() => ["qzone-login", sessionId.value]),
  queryFn: ({ signal }) =>
    resources.qzoneGet(`auth/login/${encodeURIComponent(sessionId.value)}/status`, {}, signal),
  enabled: computed(() => Boolean(sessionId.value)),
  refetchInterval: (query) => (query.state.data?.terminal || query.state.status === "error" ? false : 3000),
});

watch(() => loginStatusQuery.data.value?.status, (status) => {
  if (status === "success") void statusQuery.refetch();
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
    recordDiagnostic(qzoneError(error));
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
  },
  onError: (error) => {
    recordDiagnostic(qzoneError(error));
  },
});

const readOnlyTargetUserId = ref("");
const lastReadOnlyResult = ref<JsonRecord>({});
const readStageLabels: Record<string, string> = {
  bot_online: "检查 Bot 连接", cookie_export: "获取登录信息", identity_match: "核对 QQ 账号",
  login_page_check: "检查空间登录", self_feed_read: "读取自己的动态",
  target_feed_read: "读取好友动态", normalization_commit: "保存登录状态",
};
const readOnlyStages = computed(() => asRecords(lastReadOnlyResult.value.stages).map((stage) => ({
  key: formatValue(stage.key, "unknown"),
  label: readStageLabels[formatValue(stage.key)] ?? "检查步骤",
  status: formatValue(stage.status, "unknown"),
  code: formatValue(stage.code, "qzone_read_only_diagnostics_invalid"),
  elapsed_ms: Math.max(0, Number(stage.elapsed_ms) || 0),
  count: stage.count,
})));
const lastReadOnlySuggestion = computed(() => formatValue(lastReadOnlyResult.value.suggestion, ""));

const readOnlyMutation = useMutation({
  mutationFn: (): Promise<JsonRecord> =>
    api.post<JsonRecord>("/qzone/diagnostics/read-only", {
      bot_id: selectedBotId.value,
      target_user_id: readOnlyTargetUserId.value.trim(),
      confirm_external_read: true,
    }),
  onSuccess: (result) => {
    lastReadOnlyResult.value = asRecord(result);
    recordDiagnostic(safeDiagnostic(asRecord(result.diagnostic)));
  },
  onError: (error) => {
    recordDiagnostic(qzoneError(error));
  },
});

function runReadOnlyDiagnostics() {
  if (selectedBotId.value && !readOnlyMutation.isPending.value) {
    readOnlyMutation.mutate();
  }
}

function triggerScan(kind: "social" | "inbound") {
  const description = kind === "social" ? "检查好友动态，可能点赞、评论或转发" : "检查新留言，可能回复";
  if (selectedBotId.value && window.confirm(`Bot ${selectedBotId.value} 将${description}。现在运行吗？`)) {
    scanMutation.mutate(kind);
  }
}

// Operations state & mutation
const publishTarget = ref("");
const publishIds = new Map<string, string>();
const uncertainPublish = ref("");

const publishMutation = useMutation({
  mutationFn: (request: { bot_id: string; operation_id: string }) => resources.qzonePost("post-now", request),
  onSuccess: (result, request) => {
    const diagnostic = safeDiagnostic(asRecord(result.diagnostic ?? result));
    recordDiagnostic(diagnostic);
    if (diagnostic.outcome_unknown || ["reserved", "dispatching", "unknown"].includes(formatValue(result.status))) {
      uncertainPublish.value = diagnostic.operation_id || request.operation_id;
    } else {
      publishIds.delete(request.bot_id);
    }
  },
  onError: (error, request) => {
    const diagnostic = qzoneError(error);
    recordDiagnostic(diagnostic);
    if (diagnostic.outcome_unknown) uncertainPublish.value = diagnostic.operation_id || request.operation_id;
  },
});

function publishPost() {
  const botId = publishTarget.value;
  publishTarget.value = "";
  if (!botId || botId !== selectedBotId.value || uncertainPublish.value || publishMutation.isPending.value) return;
  const operationId = publishIds.get(botId) ?? createRandomOperationId();
  publishIds.set(botId, operationId);
  publishMutation.mutate({ bot_id: botId, operation_id: operationId });
}

// History state & mutations
const historyOperationId = ref("");
watch(() => route.query?.operation, (value) => {
  if (typeof value === "string") historyOperationId.value = value.slice(0, 96);
}, { immediate: true });
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
watch(singleOperationRecord, (operation) => {
  if (uncertainPublish.value && operation?.operation_id === uncertainPublish.value &&
    ["succeeded", "definite_failure"].includes(formatValue(operation.status))) {
    uncertainPublish.value = "";
    publishIds.delete(formatValue(operation.bot_id, ""));
  }
});

watch(selectedBotId, () => {
  publishTarget.value = "";
  sessionId.value = "";
  cookieInput.value = "";
  lastReadOnlyResult.value = {};
  diagnostics.value = [];
});

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
    recordDiagnostic(qzoneError(error));
  },
});

function resolveAbsent() {
  if (
    window.confirm(
      `请先在手机 QQ 中核对。确认操作 ${historyOperationId.value} 对应的动态没有发布？`,
    )
  ) {
    historyMutation.mutate("absent");
  }
}
</script>
