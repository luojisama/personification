<template>
  <div class="page-stack">
    <!-- 1. 配置中心 -->
    <template v-if="activeMode === 'config'">
      <PageHeader
        index="24"
        title="配置中心"
        description="按注册表分类浏览、分词搜索和类型化编辑。草稿使用 revision 一次原子保存；秘密原值不会发送到浏览器。"
      >
        <template #actions>
          <div class="config-save-actions">
            <span>{{ draftCount }} 项草稿</span>
            <button
              class="button"
              type="button"
              :disabled="draftCount === 0 || hasJsonErrors || saveMutation.isPending.value"
              @click="handleSaveDraft"
            >
              {{ saveMutation.isPending.value ? "保存中…" : "原子保存" }}
            </button>
          </div>
        </template>
      </PageHeader>

      <DiagnosticPanel
        v-if="operationError"
        :diagnostic="diagnosticFromError(operationError)"
        :default-open="true"
      />
      <DiagnosticPanel
        v-for="(diag, idx) in toolDiagnostics"
        :key="`${diag.code}:${idx}`"
        :diagnostic="diag"
        :default-open="idx === 0"
      />

      <div class="config-layout">
        <aside class="config-category-rail" aria-label="配置分类">
          <button
            type="button"
            :class="{ active: !selectedGroup }"
            @click="setGroup('')"
          >
            <span>全部配置</span>
            <b>{{ metaQuery.data.value?.total ?? 0 }}</b>
          </button>
          <button
            v-for="name in (metaQuery.data.value?.groups ?? [])"
            :key="name"
            type="button"
            :class="{ active: selectedGroup === name }"
            @click="setGroup(name)"
          >
            <span>{{ name }}</span>
            <b>{{ metaQuery.data.value?.group_counts[name] ?? 0 }}</b>
            <small v-if="metaQuery.data.value?.modified_counts[name]">
              {{ metaQuery.data.value.modified_counts[name] }} 已修改
            </small>
          </button>
        </aside>

        <div class="config-main">
          <Panel eyebrow="FILTER / CONFIG REGISTRY" title="快速筛选">
            <div class="config-search-row">
              <label class="search-label">
                <span class="sr-only">搜索配置</span>
                <input
                  v-model="searchInput"
                  type="search"
                  placeholder="配置键、中文名称、说明、供应商、模型或别名"
                  aria-label="搜索配置"
                />
              </label>
              <div class="filter-chips" role="group" aria-label="配置状态筛选">
                <button
                  v-for="item in FILTERS"
                  :key="item.key"
                  type="button"
                  :aria-pressed="activeFilters[item.key]"
                  :class="{ 'chip-active': activeFilters[item.key] }"
                  @click="toggleFilter(item.key)"
                >
                  {{ item.label }}
                </button>
              </div>
            </div>
          </Panel>

          <Panel eyebrow="MODEL / MEDIA TOOLS" title="模型与媒体配置工具">
            <div class="dossier-actions">
              <RouterLink class="button button-secondary" to="/runtime/model-tests/video-turn">
                模型与视频测试
              </RouterLink>
              <RouterLink class="button button-secondary" to="/runtime/routes/capabilities">
                查看路由能力证据
              </RouterLink>
              <button
                class="button button-secondary"
                type="button"
                @click="runConfigTool('speed', '将对已配置搜索引擎产生真实网络请求，确认继续吗？')"
              >
                搜索速度测试
              </button>
              <button
                class="button button-secondary"
                type="button"
                @click="runConfigTool('recommended', '将应用服务端推荐配置并写入配置文件，确认继续吗？')"
              >
                应用推荐配置
              </button>
            </div>
          </Panel>

          <QueryBoundary
            :pending="configQuery.isPending.value"
            :error="configQuery.error.value"
            :empty="!configQuery.data.value?.items?.length"
            empty-text="没有符合当前分类与筛选条件的配置。"
          >
            <div class="config-entry-list">
              <article
                v-for="item in configQuery.data.value?.items ?? []"
                :key="item.field_name"
                :class="['config-entry', { 'is-dirty': item.field_name in draft || item.field_name in jsonDrafts }]"
              >
                <header class="config-entry-header">
                  <div>
                    <label :for="`config-${item.field_name}`" class="config-title">
                      <span v-html="highlightText(item.display_name, debouncedSearch)" />
                    </label>
                    <code>{{ item.field_name }}</code>
                  </div>
                  <div class="config-badges">
                    <StateBadge v-if="item.secret" tone="warn">秘密</StateBadge>
                    <StateBadge v-if="item.advanced" tone="running">高级</StateBadge>
                    <StateBadge :tone="item.hot_reloadable ? 'ok' : 'warn'">
                      {{ item.hot_reloadable ? "热加载" : "需重启" }}
                    </StateBadge>
                  </div>
                </header>

                <p class="config-description" v-html="highlightText(item.description, debouncedSearch)" />

                <div class="config-editor-control">
                  <!-- 布尔开关 -->
                  <input
                    v-if="item.value_type === 'bool'"
                    :id="`config-${item.field_name}`"
                    type="checkbox"
                    :checked="getResolvedValue(item) === true || getResolvedValue(item) === 'true'"
                    @change="(e: Event) => updateDraft(item.field_name, (e.target as HTMLInputElement).checked)"
                  />

                  <!-- 下拉单选 -->
                  <select
                    v-else-if="item.choices && item.choices.length > 0"
                    :id="`config-${item.field_name}`"
                    :value="String(getResolvedValue(item) ?? '')"
                    @change="(e: Event) => updateDraft(item.field_name, (e.target as HTMLSelectElement).value)"
                  >
                    <option v-for="choice in item.choices" :key="choice" :value="choice">
                      {{ choice }}
                    </option>
                  </select>

                  <div
                    v-else-if="item.value_type === 'list' || item.value_type === 'dict' || item.value_type === 'json'"
                    class="config-json-editor"
                  >
                    <textarea
                      :id="`config-${item.field_name}`"
                      rows="4"
                      :value="getJsonDisplayValue(item)"
                      :aria-invalid="Boolean(jsonErrors[item.field_name])"
                      :aria-describedby="jsonErrors[item.field_name] ? `config-${item.field_name}-error` : undefined"
                      @input="(e: Event) => handleJsonInput(item, (e.target as HTMLTextAreaElement).value)"
                    />
                    <small
                      v-if="jsonErrors[item.field_name]"
                      :id="`config-${item.field_name}-error`"
                      role="alert"
                      class="config-field-error"
                    >
                      {{ jsonErrors[item.field_name] }}
                    </small>
                  </div>

                  <!-- 数值类型 -->
                  <input
                    v-else-if="item.value_type === 'int' || item.value_type === 'float'"
                    :id="`config-${item.field_name}`"
                    type="number"
                    :min="item.min_value ?? undefined"
                    :max="item.max_value ?? undefined"
                    :step="item.value_type === 'int' ? '1' : 'any'"
                    :value="String(getResolvedValue(item) ?? '')"
                    @input="(e: Event) => updateDraft(item.field_name, (e.target as HTMLInputElement).value === '' ? '' : Number((e.target as HTMLInputElement).value))"
                  />

                  <!-- 多行文本 -->
                  <textarea
                    v-else-if="item.value_type === 'textarea'"
                    :id="`config-${item.field_name}`"
                    rows="4"
                    :value="String(getResolvedValue(item) ?? '')"
                    @input="(e: Event) => updateDraft(item.field_name, (e.target as HTMLTextAreaElement).value)"
                  />

                  <!-- 普通文本与密钥 -->
                  <input
                    v-else
                    :id="`config-${item.field_name}`"
                    :type="item.secret ? 'password' : 'text'"
                    :value="String(getResolvedValue(item) ?? '')"
                    :autocomplete="item.secret ? 'new-password' : 'off'"
                    @input="(e: Event) => updateDraft(item.field_name, (e.target as HTMLInputElement).value)"
                  />

                  <button
                    v-if="item.field_name in draft || item.field_name in jsonDrafts"
                    type="button"
                    class="button button-quiet"
                    @click="resetDraft(item.field_name)"
                  >
                    撤销草稿
                  </button>
                </div>

                <small
                  v-if="item.min_value != null || item.max_value != null || item.aliases?.length"
                  class="config-meta-info"
                >
                  范围 {{ item.min_value ?? "−∞" }} – {{ item.max_value ?? "+∞" }}
                  <template v-if="item.aliases?.length"> · 别名 {{ item.aliases.join("、") }}</template>
                </small>
              </article>
            </div>
          </QueryBoundary>

          <div v-if="configQuery.data.value && configQuery.data.value.total_pages > 1" class="pagination">
            <button
              type="button"
              :disabled="currentPage <= 1"
              @click="setPage(currentPage - 1)"
            >
              上一页
            </button>
            <span>第 {{ currentPage }} / {{ configQuery.data.value.total_pages }} 页</span>
            <button
              type="button"
              :disabled="currentPage >= configQuery.data.value.total_pages"
              @click="setPage(currentPage + 1)"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </template>

    <!-- 2. 系统设置 -->
    <template v-else-if="activeMode === 'settings'">
      <PageHeader
        index="33"
        title="设置"
        description="控制浏览器侧主题与动效偏好；服务端配置只展示安全状态，不在本页回显 Secret 或原始配置包。"
      />

      <div class="settings-grid">
        <Panel class="wide-panel" eyebrow="APPEARANCE / LOCAL" title="取证台主题">
          <ThemeSwitcher />
        </Panel>

        <Panel eyebrow="ACCESSIBILITY / MOTION" title="动效与键盘">
          <ul class="settings-notes">
            <li>
              <StateBadge tone="ok">120–220 ms</StateBadge>
              <span>常规状态切换保持短促，不制造等待假象。</span>
            </li>
            <li>
              <StateBadge tone="ok">系统联动</StateBadge>
              <span>启用“减少动态效果”后，过渡与动画自动缩短到 1 ms。</span>
            </li>
            <li>
              <StateBadge tone="ok">焦点可见</StateBadge>
              <span>导航、按钮、筛选和诊断折叠均保留键盘焦点轮廓。</span>
            </li>
          </ul>
        </Panel>

        <Panel eyebrow="RUNTIME / SAFE VIEW" title="服务端配置状态">
          <QueryBoundary
            :pending="settingsQuery.isPending.value"
            :error="settingsQuery.error.value"
          >
            <dl class="safe-settings-view">
              <div>
                <dt>API 前缀</dt>
                <dd><code>/personification/api/v2</code></dd>
              </div>
              <div>
                <dt>实时协议</dt>
                <dd>SSE + Last-Event-ID</dd>
              </div>
              <div>
                <dt>配置版本</dt>
                <dd>
                  <code>{{ formatRevision(settingsQuery.data.value?.revision) }}</code>
                </dd>
              </div>
              <div>
                <dt>参与策略</dt>
                <dd>{{ formatParticipation(settingsQuery.data.value?.participation_v2_mode) }}</dd>
              </div>
            </dl>
          </QueryBoundary>
        </Panel>

        <Panel class="wide-panel" eyebrow="SECURITY / DISPLAY" title="可见数据边界">
          <div class="security-manifest">
            <p>此管理台只消费服务端白名单 DTO。Trace 详情不会读取隐藏推理、完整 Tool 参数、原始 Tool 结果、Provider 请求/响应、Cookie、API Key 或媒体 Token。</p>
            <code>frontend_trace_allowlist_v1</code>
          </div>
        </Panel>
      </div>
    </template>

    <!-- 3. 插件日志 -->
    <template v-else-if="activeMode === 'logs'">
      <PageHeader
        index="29"
        title="插件日志"
        description="实时 SSE、历史游标搜索、Trace 过滤和明确确认的清理操作分开呈现；事件 payload 已由服务端脱敏。"
      >
        <template v-if="logSection !== 'cleanup'" #actions>
          <div class="search-field">
            <input
              v-model="logSearchInput"
              type="search"
              placeholder="搜索级别、来源、Trace 或安全摘要"
              aria-label="搜索日志"
            />
          </div>
        </template>
      </PageHeader>

      <DiagnosticPanel
        v-if="clearLogsMutation.data.value"
        :diagnostic="safeDiagnostic(clearLogsMutation.data.value)"
        :default-open="true"
      />

      <div class="segmented-control" role="tablist" aria-label="日志板块导航">
        <button
          type="button"
          role="tab"
          :aria-selected="logSection === 'live'"
          @click="navigateLogSection('live')"
        >
          实时流
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="logSection === 'history'"
          @click="navigateLogSection('history')"
        >
          历史日志
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="logSection === 'cleanup'"
          @click="navigateLogSection('cleanup')"
        >
          日志清理
        </button>
      </div>

      <Panel
        v-if="logSection !== 'cleanup'"
        :eyebrow="`LOGS / ${logSection.toUpperCase()}`"
        :title="logSection === 'live' ? `实时流 · SSE ${sseStatus}` : '历史日志'"
      >
        <QueryBoundary
          :pending="logsQuery.isPending.value && logSection !== 'live'"
          :error="logsQuery.error.value"
          :empty="currentLogRows.length === 0"
          :empty-text="logSection === 'live' ? '当前 SSE 窗口没有日志事件。' : '当前筛选没有历史日志。'"
        >
          <div class="table-responsive">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">时间</th>
                  <th scope="col">级别</th>
                  <th scope="col">来源</th>
                  <th scope="col">安全摘要</th>
                  <th scope="col">Trace</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, idx) in currentLogRows" :key="String(row.id || row.ts || idx)">
                  <td>{{ formatDateTime(row.ts as string | number) }}</td>
                  <td>
                    <StateBadge :tone="getLogLevelTone(row.level || row.status)">
                      {{ row.level || row.status || "INFO" }}
                    </StateBadge>
                  </td>
                  <td>{{ row.source || row.logger || row.topic || "—" }}</td>
                  <td>{{ row.message || row.summary || row.code || "—" }}</td>
                  <td>
                    <code v-if="row.trace_id">{{ row.trace_id }}</code>
                    <span v-else>—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </QueryBoundary>

        <div v-if="logSection === 'history' && logsQuery.data.value" class="pagination">
          <button
            type="button"
            :disabled="cursorStack.length <= 1"
            @click="popCursor"
          >
            较新
          </button>
          <span>游标页 {{ cursorStack.length }}</span>
          <button
            type="button"
            :disabled="!logsQuery.data.value.has_more || !logsQuery.data.value.next_cursor"
            @click="pushCursor(logsQuery.data.value.next_cursor)"
          >
            较早
          </button>
        </div>
      </Panel>

      <Panel v-if="logSection === 'cleanup'" eyebrow="LOGS / CLEANUP" title="清理插件日志">
        <div class="cleanup-box">
          <p>该操作只清理插件管理日志，不影响 Trace 数据库。输入 <code>CLEAR LOGS</code> 才能提交。</p>
          <div class="inline-controls">
            <input
              v-model="cleanupConfirmation"
              type="text"
              placeholder="输入 CLEAR LOGS 确认"
              aria-label="清理确认文字"
              :aria-invalid="cleanupConfirmation !== '' && cleanupConfirmation !== 'CLEAR LOGS'"
            />
            <button
              class="button button-danger"
              type="button"
              :disabled="cleanupConfirmation !== 'CLEAR LOGS' || clearLogsMutation.isPending.value"
              @click="handleClearLogs"
            >
              {{ clearLogsMutation.isPending.value ? "清理中…" : "清理日志" }}
            </button>
          </div>
        </div>
      </Panel>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/vue-query";
import { resources } from "@/api/resources";
import { diagnosticFromError, safeDiagnostic } from "@/api/diagnostics";
import type { ConfigListItem, OperationDiagnostic, CursorPage, CatalogItem } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import { useRuntimeEvents } from "@vue-app/realtime/runtimeEvents";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import DiagnosticPanel from "@vue-app/components/DiagnosticPanel.vue";
import ThemeSwitcher from "@vue-app/components/ThemeSwitcher.vue";

const props = withDefaults(defineProps<{ mode?: "config" | "settings" | "logs" }>(), {
  mode: "config",
});

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const runtimeEvents = useRuntimeEvents();

const activeMode = computed(() => props.mode || (route.meta.mode as "config" | "settings" | "logs") || "config");

/* ==========================================================================
   1. 配置中心 (Config Center) 状态与逻辑
   ========================================================================== */
type FilterKey = "modified" | "restart_required" | "hot_reloadable" | "advanced" | "secret" | "invalid";
const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "modified", label: "仅已修改" },
  { key: "restart_required", label: "需要重启" },
  { key: "hot_reloadable", label: "支持热加载" },
  { key: "advanced", label: "高级配置" },
  { key: "secret", label: "秘密配置" },
  { key: "invalid", label: "验证错误" },
];

const currentPage = computed(() => Math.max(1, Number(route.query.page ?? 1) || 1));
const searchInput = ref(String(route.query.search ?? ""));
const debouncedSearch = ref(searchInput.value);
const selectedGroup = computed(() => String(route.query.group ?? ""));
const activeFilters = computed<Record<FilterKey, boolean>>(() => ({
  modified: route.query.modified === "1",
  restart_required: route.query.restart_required === "1",
  hot_reloadable: route.query.hot_reloadable === "1",
  advanced: route.query.advanced === "1",
  secret: route.query.secret === "1",
  invalid: route.query.invalid === "1",
}));

let searchTimer: number | null = null;
watch(searchInput, (val) => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    debouncedSearch.value = val;
    setQueryParam("search", val || undefined);
  }, 300);
});

const draft = ref<Record<string, unknown>>({});
const jsonDrafts = ref<Record<string, string>>({});
const jsonErrors = ref<Record<string, string>>({});
const hasJsonErrors = computed(() => Object.keys(jsonErrors.value).length > 0);
const draftCount = computed(() => Object.keys(draft.value).length);
const operationError = ref<unknown>(null);
const toolDiagnostics = ref<OperationDiagnostic[]>([]);

function setQueryParam(key: string, value?: string) {
  const next = { ...route.query };
  if (value) next[key] = value;
  else delete next[key];
  if (key !== "page") next.page = "1";
  router.push({ query: next });
}
function setGroup(group: string) { setQueryParam("group", group || undefined); }
function setPage(p: number) { setQueryParam("page", String(p)); }
function toggleFilter(key: FilterKey) {
  setQueryParam(key, activeFilters.value[key] ? undefined : "1");
}

const metaQuery = useQuery({
  queryKey: ["config-meta"],
  queryFn: ({ signal }) => resources.configMetadata(signal),
  staleTime: 60_000,
  enabled: computed(() => activeMode.value === "config"),
});

const configQuery = useQuery({
  queryKey: computed(() => [
    "config-center",
    currentPage.value,
    debouncedSearch.value,
    selectedGroup.value,
    activeFilters.value,
  ]),
  queryFn: ({ signal }) =>
    resources.config(
      currentPage.value,
      20,
      { search: debouncedSearch.value, group: selectedGroup.value, ...activeFilters.value },
      signal,
    ),
  enabled: computed(() => activeMode.value === "config"),
});

function getResolvedValue(item: ConfigListItem) {
  return item.field_name in draft.value ? draft.value[item.field_name] : item.value;
}
function updateDraft(field: string, val: unknown) {
  draft.value = { ...draft.value, [field]: val };
}
function getJsonDisplayValue(item: ConfigListItem): string {
  if (item.field_name in jsonDrafts.value) return jsonDrafts.value[item.field_name] ?? "";
  const resolved = getResolvedValue(item);
  if (resolved === undefined || resolved === null) return "";
  if (typeof resolved === "string") return resolved;
  try {
    return JSON.stringify(resolved, null, 2);
  } catch {
    return String(resolved);
  }
}
function handleJsonInput(item: ConfigListItem, text: string) {
  const field = item.field_name;
  jsonDrafts.value = { ...jsonDrafts.value, [field]: text };
  if (!text.trim()) {
    const nextErrors = { ...jsonErrors.value };
    delete nextErrors[field];
    jsonErrors.value = nextErrors;
    updateDraft(field, null);
    return;
  }
  try {
    const parsed = JSON.parse(text);
    if (item.value_type === "list" && !Array.isArray(parsed)) {
      throw new Error("配置值必须是 JSON 数组（列表）");
    }
    if (
      item.value_type === "dict"
      && (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
    ) {
      throw new Error("配置值必须是 JSON 键值对对象（字典）");
    }
    const nextErrors = { ...jsonErrors.value };
    delete nextErrors[field];
    jsonErrors.value = nextErrors;
    updateDraft(field, parsed);
  } catch (error) {
    jsonErrors.value = {
      ...jsonErrors.value,
      [field]: error instanceof Error ? error.message : "JSON 格式解析错误",
    };
    const nextDraft = { ...draft.value };
    delete nextDraft[field];
    draft.value = nextDraft;
  }
}
function resetDraft(field: string) {
  const next = { ...draft.value };
  delete next[field];
  draft.value = next;
  const nextJson = { ...jsonDrafts.value };
  delete nextJson[field];
  jsonDrafts.value = nextJson;
  const nextErrors = { ...jsonErrors.value };
  delete nextErrors[field];
  jsonErrors.value = nextErrors;
}

const saveMutation = useMutation({
  mutationFn: () => {
    const revision = configQuery.data.value?.revision ?? metaQuery.data.value?.revision ?? "";
    return resources.patchConfig(revision, draft.value);
  },
  onSuccess: () => {
    draft.value = {};
    jsonDrafts.value = {};
    jsonErrors.value = {};
    operationError.value = null;
    void queryClient.invalidateQueries({ queryKey: ["config-center"] });
    void queryClient.invalidateQueries({ queryKey: ["config-meta"] });
  },
  onError: (err) => { operationError.value = err; },
});

function handleSaveDraft() {
  if (window.confirm("确认将当前草稿原子写入服务端配置？包含需重启项时需重启服务生效。")) {
    saveMutation.mutate();
  }
}

async function runConfigTool(kind: "speed" | "recommended", message: string) {
  if (!window.confirm(message)) return;
  try {
    const result = kind === "speed"
      ? await resources.searchEngineSpeedTest()
      : await resources.applyRecommendedConfig();
    toolDiagnostics.value = [result, ...toolDiagnostics.value].slice(0, 5);
    operationError.value = null;
  } catch (caught) {
    operationError.value = caught;
  }
}

function highlightText(text: string, needle: string): string {
  if (!needle || !text) return text || "";
  const tokens = needle.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return text;
  const exp = new RegExp(`(${tokens.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.replace(exp, "<mark>$1</mark>");
}

/* ==========================================================================
   2. 系统设置 (Settings) 状态与逻辑
   ========================================================================== */
const settingsQuery = useQuery({
  queryKey: ["settings"],
  queryFn: ({ signal }) => resources.runtimeSettings(signal),
  enabled: computed(() => activeMode.value === "settings"),
});

function formatRevision(rev: unknown): string {
  return typeof rev === "string" || typeof rev === "number" ? String(rev) : "未提供";
}
function formatParticipation(mode: unknown): string {
  return typeof mode === "string" ? `影子开关（${mode}）` : "未提供";
}

/* ==========================================================================
   3. 插件日志 (Plugin Logs) 状态与逻辑
   ========================================================================== */
const logSection = computed(() => (route.params.section as string) || "live");
const logSearchInput = ref("");
const debouncedLogSearch = ref("");
let logSearchTimer: number | null = null;
watch(logSearchInput, (val) => {
  if (logSearchTimer) clearTimeout(logSearchTimer);
  logSearchTimer = window.setTimeout(() => {
    debouncedLogSearch.value = val;
    cursorStack.value = [0];
  }, 300);
});

const cursorStack = ref<number[]>([0]);
const currentCursor = computed(() => cursorStack.value[cursorStack.value.length - 1] ?? 0);
const cleanupConfirmation = ref("");
const sseStatus = computed<"connected" | "connecting" | "idle">(() => {
  if (runtimeEvents.state.value === "open") return "connected";
  if (runtimeEvents.state.value === "connecting" || runtimeEvents.state.value === "retrying") return "connecting";
  return "idle";
});
const liveEvents = computed<CatalogItem[]>(() => {
  const result: CatalogItem[] = [];
  for (let index = runtimeEvents.events.value.length - 1; index >= 0; index -= 1) {
    const event = runtimeEvents.events.value[index];
    if (!event || event.topic !== "log.appended") continue;
    result.push({
      ...event.payload,
      id: event.id,
      ts: event.ts,
      topic: event.topic,
      trace_id: event.trace_id ?? event.payload.trace_id,
    });
    if (result.length >= 100) break;
  }
  return result;
});

const logsQuery = useQuery<CursorPage<CatalogItem>>({
  queryKey: computed(() => ["logs", currentCursor.value, debouncedLogSearch.value]),
  queryFn: ({ signal }) => resources.logs(100, currentCursor.value, debouncedLogSearch.value, signal),
  enabled: computed(() => activeMode.value === "logs" && logSection.value === "history"),
});

const clearLogsMutation = useMutation({
  mutationFn: () => resources.clearLogs(),
});

function handleClearLogs() {
  if (cleanupConfirmation.value !== "CLEAR LOGS") return;
  if (window.confirm("警告：确认清理插件管理日志？此操作不可逆。")) {
    clearLogsMutation.mutate();
  }
}

function navigateLogSection(sec: string) {
  router.push(`/operations/logs/${sec}`);
}
function pushCursor(next: number) { cursorStack.value.push(next); }
function popCursor() { if (cursorStack.value.length > 1) cursorStack.value.pop(); }

const currentLogRows = computed(() => {
  if (logSection.value === "live") return liveEvents.value;
  return logsQuery.data.value?.items ?? [];
});

function getLogLevelTone(lvl: unknown): "ok" | "warn" | "error" | "running" | "unknown" {
  const s = String(lvl || "").toLowerCase();
  if (s === "error" || s === "critical") return "error";
  if (s === "warn" || s === "warning") return "warn";
  if (s === "info" || s === "ok" || s === "success") return "ok";
  return "unknown";
}

</script>
