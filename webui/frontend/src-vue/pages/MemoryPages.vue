<template>
  <div class="page-stack memory-pages">
    <PageHeader
      index="14"
      title="Agent 记忆与记忆宫殿"
      description="最近记忆、召回测试、内部状态、关联图谱、宫殿分区与向量索引；详情按需加载，敏感内容脱敏呈现。"
    >
      <template v-if="showSearch" #actions>
        <TextField
          :id="searchInputId"
          v-model="searchTerm"
          class="search-field"
          :label="searchLabel"
          type="search"
          :placeholder="searchPlaceholder"
          @keydown.enter="handleSearchSubmit"
        />
      </template>
    </PageHeader>

    <details class="memory-navigation-details">
    <summary>更多记忆视图</summary>
    <nav class="segmented-control memory-nav" aria-label="记忆功能模块切换">
      <router-link
        v-for="item in navSections"
        :key="item.key"
        :to="item.to"
        class="nav-tab"
        :aria-current="currentSection === item.key ? 'page' : undefined"
      >
        {{ item.label }}
      </router-link>
    </nav>
    </details>

    <!-- 1. 最近记忆 Recent Section -->
    <Panel
      v-if="currentSection === 'recent'"
      eyebrow="MEMORY / RECENT"
      title="最近记忆"
    >
      <QueryBoundary :pending="recentQuery.isPending.value" :error="recentQuery.error.value">
        <div v-if="memoriesList.length === 0" class="empty-notice">
          当前没有匹配的记忆记录。
        </div>
        <div v-else class="table-responsive">
          <table class="data-table" aria-label="最近记忆列表">
            <thead>
              <tr>
                <th scope="col">记忆摘要 / ID</th>
                <th scope="col">作用域</th>
                <th scope="col">来源</th>
                <th scope="col">状态</th>
                <th scope="col">过期时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in memoriesList" :key="textAt(row, 'memory_id', 'id') || idx">
                <td>
                  <CollapsibleText :text="textAt(row, 'summary', 'content_summary', 'text') || '（无摘要）'" :limit="80" />
                  <br />
                  <code>{{ textAt(row, 'memory_id', 'id') || '—' }}</code>
                </td>
                <td :title="rawMemoryScope(row) || undefined">{{ memoryScopeLabel(rawMemoryScope(row)) }}</td>
                <td :title="rawMemorySource(row) || undefined">{{ memorySourceLabel(rawMemorySource(row)) }}</td>
                <td>
                  <StateBadge :tone="badgeTone(rawMemoryStatus(row))" :raw="rawMemoryStatus(row)">
                    {{ memoryStatusLabel(rawMemoryStatus(row)) }}
                  </StateBadge>
                </td>
                <td>{{ formatDateTime(row.expires_at as string | number | null) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <Pagination v-if="recentQuery.data.value" :page="page" :total-pages="recentQuery.data.value.total_pages" :total="recentQuery.data.value.total" :disabled="recentQuery.isFetching.value" @update:page="page = $event" />
      </QueryBoundary>
    </Panel>

    <!-- 2. 召回测试 Search Section -->
    <Panel
      v-else-if="currentSection === 'search'"
      eyebrow="MEMORY / SEARCH"
      title="召回测试结果"
    >
      <QueryBoundary :pending="searchQuery.isPending.value" :error="searchQuery.error.value">
        <div v-if="!searchTerm.trim()" class="empty-notice">
          请输入查询关键词后按回车执行召回测试。
        </div>
        <div v-else-if="searchResults.length === 0" class="empty-notice">
          未召回到与关键词相关的记忆。
        </div>
        <div v-else class="search-result-grid">
          <article
            v-for="(item, idx) in searchResults"
            :key="textAt(item, 'memory_id', 'id') || idx"
            class="search-result-card"
          >
            <header class="result-header">
              <span class="score-badge">相似度: {{ formatScore(item.score ?? item.similarity) }}</span>
              <StateBadge :tone="badgeTone(rawMemoryStatus(item))" :raw="rawMemoryStatus(item)">
                {{ memoryStatusLabel(rawMemoryStatus(item)) }}
              </StateBadge>
            </header>
            <CollapsibleText :text="textAt(item, 'summary', 'content', 'text') || '—'" :limit="240" />
            <footer class="result-meta">
              <code>{{ textAt(item, 'memory_id', 'id') }}</code>
              <span :title="rawMemoryScope(item) || undefined">作用域: {{ memoryScopeLabel(rawMemoryScope(item)) }}</span>
              <span>更新: {{ formatDateTime(item.updated_at as string | number | null) }}</span>
            </footer>
          </article>
        </div>
      </QueryBoundary>
    </Panel>

    <!-- 3. 内部状态 Inner State Section -->
    <Panel
      v-else-if="currentSection === 'inner-state'"
      eyebrow="MEMORY / INNER-STATE"
      title="Agent 内部心智与工作记忆"
    >
      <QueryBoundary :pending="businessQuery.isPending.value" :error="businessQuery.error.value">
        <div class="inner-state-grid">
          <article class="state-metric-card">
            <span class="metric-label">当前情绪状态</span>
            <strong>{{ textAt(businessRecord, 'mood', 'emotion') || '未知' }}</strong>
            <small>能级: {{ textAt(businessRecord, 'energy', 'vitality') || '未配置' }}</small>
          </article>
          <article class="state-metric-card">
            <span class="metric-label">工作记忆负荷</span>
            <strong>{{ textAt(businessRecord, 'working_memory_count', 'load') || '未知' }} 项</strong>
            <small>待巩固项目: {{ textAt(businessRecord, 'pending_consolidation', 'pending_count') || '未知' }}</small>
          </article>
          <article class="state-metric-card">
            <span class="metric-label">长期记忆沉淀</span>
            <strong>{{ textAt(businessRecord, 'total_memories', 'consolidated_count') || '—' }}</strong>
            <small>最近同步: {{ formatDateTime(businessRecord.updated_at as string | number | null) }}</small>
          </article>
        </div>

        <div v-if="Array.isArray(businessRecord.active_contexts)" class="active-context-section">
          <h3>活跃上下文线索</h3>
          <ul class="context-pill-list">
            <li v-for="(ctx, idx) in (businessRecord.active_contexts as unknown[])" :key="idx" class="context-pill">
              {{ typeof ctx === 'string' ? ctx : textAt(asRecord(ctx), 'summary', 'name') }}
            </li>
          </ul>
        </div>
      </QueryBoundary>
    </Panel>

    <!-- 4. 记忆图谱 Graph Section -->
    <Panel
      v-else-if="currentSection === 'graph'"
      eyebrow="MEMORY / GRAPH"
      title="概念与实体关联图谱"
    >
      <QueryBoundary :pending="businessQuery.isPending.value" :error="businessQuery.error.value">
        <div class="graph-overview-stats">
          <div class="graph-stat-pill">
            <span>实体节点数</span>
            <strong>{{ textAt(businessRecord, 'node_count', 'total_nodes') || graphNodes.length }}</strong>
          </div>
          <div class="graph-stat-pill">
            <span>关联关系边</span>
            <strong>{{ textAt(businessRecord, 'edge_count', 'total_edges') || graphEdges.length }}</strong>
          </div>
          <div class="graph-stat-pill">
            <span>概念聚类数</span>
            <strong>{{ textAt(businessRecord, 'cluster_count') || '0' }}</strong>
          </div>
        </div>

        <div class="graph-entities-grid">
          <article
            v-for="(node, idx) in graphNodes"
            :key="textAt(node, 'id', 'name') || idx"
            class="graph-node-card"
          >
            <header class="node-head">
              <span class="node-type-tag">{{ textAt(node, 'type', 'category') || '实体' }}</span>
              <h4>{{ textAt(node, 'name', 'label', 'id') }}</h4>
            </header>
            <p class="node-desc">{{ textAt(node, 'description', 'summary') || '暂无描述' }}</p>
            <div class="node-weight-bar">
              <span class="bar-label">权重: {{ textAt(node, 'weight', 'importance') || '1.0' }}</span>
              <div class="weight-track">
                <div class="weight-fill" :style="{ width: `${Math.min(100, (Number(node.weight || 1) * 20))}%` }" />
              </div>
            </div>
          </article>
        </div>
      </QueryBoundary>
    </Panel>

    <!-- 5. 记忆宫殿分区 Palace Zones Section -->
    <Panel
      v-else-if="currentSection === 'palace-zones'"
      eyebrow="MEMORY / PALACE-ZONES"
      title="记忆宫殿分区布局"
    >
      <QueryBoundary :pending="businessQuery.isPending.value" :error="businessQuery.error.value">
        <div v-if="palaceZoneCards.length" class="palace-grid">
          <article
            v-for="(zone, idx) in palaceZoneCards"
            :key="textAt(zone, 'zone_id', 'id', 'name') || idx"
            :class="['palace-card', { 'is-selected': selectedPalaceZoneId === palaceZoneId(zone) }]"
          >
            <header class="palace-card-header">
              <div>
                <span class="zone-id-tag">ZONE / {{ textAt(zone, 'zone_id', 'code') || idx + 1 }}</span>
                <h3>{{ textAt(zone, 'name', 'title') || '未命名殿室' }}</h3>
              </div>
              <StateBadge :tone="badgeTone(textAt(zone, 'status'))">
                {{ palaceStatusLabel(textAt(zone, 'status')) }}
              </StateBadge>
            </header>
            <p class="zone-desc">{{ textAt(zone, 'description', 'purpose') || '未分配具体职能。' }}</p>
            <dl class="zone-meta-list">
              <div>
                <dt>条目数 / 容量</dt>
                <dd>{{ textAt(zone, 'item_count', 'anchor_count', 'items_count') || '0' }} / {{ textAt(zone, 'capacity') || '未配置' }}</dd>
              </div>
              <div>
                <dt>最近更新</dt>
                <dd>{{ formatDateTime(zoneUpdatedAt(zone)) }}</dd>
              </div>
            </dl>
            <button
              class="button button-secondary palace-zone-button"
              type="button"
              :aria-pressed="selectedPalaceZoneId === palaceZoneId(zone)"
              :aria-label="`查看${textAt(zone, 'name', 'title') || '当前'}分区详情`"
              @click="selectedPalaceZoneId = palaceZoneId(zone)"
            >
              {{ selectedPalaceZoneId === palaceZoneId(zone) ? "正在查看分区详情" : "查看分区详情" }}
            </button>
          </article>
        </div>
        <div v-else class="empty-notice">记忆宫殿当前没有可展示的分区。</div>

        <Panel v-if="selectedPalaceZone" eyebrow="MEMORY / PALACE-DETAIL" :title="`${textAt(selectedPalaceZone, 'name', 'title') || '未命名殿室'}详情`">
          <dl class="compact-kv">
            <div><dt>条目数</dt><dd>{{ textAt(selectedPalaceZone, 'item_count', 'count', 'anchor_count') || '—' }}</dd></div>
            <div><dt>更新时间</dt><dd>{{ formatDateTime(zoneUpdatedAt(selectedPalaceZone)) }}</dd></div>
          </dl>

          <QueryBoundary :pending="zoneEntriesQuery.isPending.value" :error="zoneEntriesQuery.error.value">
          <div v-if="selectedZoneEntries.length" class="table-responsive">
            <table class="data-table" aria-label="记忆宫殿分区条目">
              <thead>
                <tr>
                  <th scope="col">条目摘要 / ID</th>
                  <th scope="col">状态</th>
                  <th scope="col">更新时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(entry, index) in selectedZoneEntries" :key="textAt(entry, 'memory_id', 'id') || index">
                  <td>
                    <CollapsibleText :text="textAt(entry, 'summary', 'title', 'label') || '（未提供摘要）'" :limit="180" />
                    <br />
                    <code>{{ textAt(entry, 'memory_id', 'id') || '—' }}</code>
                  </td>
                  <td>
                    <StateBadge :tone="badgeTone(rawMemoryStatus(entry))" :raw="rawMemoryStatus(entry)">
                      {{ memoryStatusLabel(rawMemoryStatus(entry)) }}
                    </StateBadge>
                  </td>
                  <td>{{ formatDateTime(entry.updated_at as string | number | null) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty-notice">当前分区暂无可展示条目。</p>
          <Pagination v-if="zoneEntriesQuery.data.value" :page="zonePage" :total-pages="zoneEntriesQuery.data.value.total_pages" :total="zoneEntriesQuery.data.value.total" :disabled="zoneEntriesQuery.isFetching.value" @update:page="zonePage = $event" />
          </QueryBoundary>
        </Panel>
      </QueryBoundary>
    </Panel>

    <!-- 6. 向量索引 Vector Index Section -->
    <Panel
      v-else-if="currentSection === 'vector-index'"
      eyebrow="MEMORY / VECTOR"
      title="向量索引状态与维护"
    >
      <template #actions>
        <button
          class="button button-danger"
          type="button"
          :disabled="rebuildMutation.isPending.value"
          @click="handleRebuildPrompt"
        >
          {{ rebuildMutation.isPending.value ? '正在触发重建…' : '重建索引' }}
        </button>
      </template>

      <QueryBoundary :pending="businessQuery.isPending.value" :error="businessQuery.error.value">
        <dl class="detail-list memory-vector-details">
          <div>
            <dt>索引状态</dt>
            <dd>
              <StateBadge :tone="badgeTone(textAt(businessRecord, 'status', 'state'))">
                {{ textAt(businessRecord, 'status', 'state') || '未知' }}
              </StateBadge>
            </dd>
          </div>
          <div>
            <dt>已索引文档数</dt>
            <dd><strong>{{ textAt(businessRecord, 'document_count', 'count', 'total') || '0' }}</strong></dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{{ formatDateTime(businessRecord.updated_at as string | number | null) }}</dd>
          </div>
          <div>
            <dt>诊断代码</dt>
            <dd><code>{{ textAt(businessRecord, 'diagnostic_code', 'code') || '未提供诊断代码' }}</code></dd>
          </div>
        </dl>

        <section class="memory-embedding-status" aria-labelledby="embedding-status-title">
          <h3 id="embedding-status-title">Embedding API 索引状态</h3>
          <p class="muted">这是本地索引与队列观察结果，不会主动请求 Provider，因此“未核验”不表示连接正常。</p>
          <dl class="detail-list">
            <div>
              <dt>运行状态</dt>
              <dd><StateBadge :tone="embeddingTone"><span :title="embeddingState">{{ embeddingStateLabel }}</span></StateBadge></dd>
            </div>
            <div>
              <dt>连通性</dt>
              <dd><StateBadge :tone="embeddingConnectivityTone"><span :title="embeddingConnectivity">{{ embeddingConnectivityLabel }}</span></StateBadge></dd>
            </div>
            <div><dt>已索引 / 可召回</dt><dd>{{ embeddingIndexed }} / {{ embeddingTotal }}</dd></div>
            <div><dt>待处理</dt><dd>{{ embeddingPending }}</dd></div>
            <div><dt>重建中</dt><dd>{{ embeddingRebuilding ? '是' : '否' }}</dd></div>
            <div v-if="embeddingProvider"><dt>Provider</dt><dd>{{ embeddingProvider }}</dd></div>
            <div v-if="embeddingModel"><dt>模型</dt><dd><code>{{ embeddingModel }}</code></dd></div>
            <div v-if="embeddingDimension"><dt>向量维度</dt><dd>{{ embeddingDimension }}</dd></div>
            <div><dt>诊断代码</dt><dd><code>{{ embeddingDiagnosticCode }}</code></dd></div>
          </dl>
        </section>
        <section class="memory-embedding-status" aria-labelledby="text-index-status-title">
          <h3 id="text-index-status-title">算法检索文本索引</h3>
          <QueryBoundary :pending="textIndexQuery.isPending.value" :error="textIndexQuery.error.value">
            <dl class="detail-list" v-if="textIndexQuery.data.value">
              <div><dt>检索模式</dt><dd><code>{{ textAt(textIndexQuery.data.value, 'mode') }}</code></dd></div>
              <div><dt>索引版本</dt><dd><code>{{ textAt(textIndexQuery.data.value, 'version') }}</code></dd></div>
              <div><dt>覆盖率</dt><dd>{{ textAt(textIndexQuery.data.value, 'indexed') }} / {{ textAt(textIndexQuery.data.value, 'total') }}</dd></div>
              <div><dt>待建立</dt><dd>{{ textAt(textIndexQuery.data.value, 'pending') }}</dd></div>
            </dl>
          </QueryBoundary>
        </section>

        <div v-if="rebuildFeedback" class="rebuild-banner" :class="{ 'banner-success': rebuildSuccess }">
          <p>{{ rebuildFeedback }}</p>
        </div>
      </QueryBoundary>
    </Panel>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useMutation, useQuery } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import type { CatalogItem, Page } from "@/api/types";
import PageHeader from "@vue-app/components/PageHeader.vue";
import CollapsibleText from "@vue-app/components/CollapsibleText.vue";
import Pagination from "@vue-app/components/Pagination.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { formatDateTime } from "@/lib/format";

const route = useRoute();

const currentSection = computed(() => {
  const raw = String(route.params.section || "");
  if (route.name === "persona-memory-palace" && !raw) return "palace-zones";
  return raw || "recent";
});

const navSections = [
  { key: "recent", label: "最近记忆", to: "/persona/memories/recent" },
  { key: "search", label: "召回测试", to: "/persona/memories/search" },
  { key: "inner-state", label: "内部状态", to: "/persona/memories/inner-state" },
  { key: "graph", label: "关联图谱", to: "/persona/memories/graph" },
  { key: "palace-zones", label: "宫殿分区", to: "/persona/memory-palace/palace-zones" },
  { key: "vector-index", label: "向量索引", to: "/persona/memories/vector-index" },
];

const page = ref(1);
const selectedBotId = computed(() => String(route.query.bot_id ?? ""));
const searchTerm = ref("");
const appliedSearch = ref("");
const searchInputId = "memory-search-input";

const showSearch = computed(() => currentSection.value === "recent" || currentSection.value === "search");
const searchLabel = computed(() => (currentSection.value === "search" ? "召回查询" : "搜索记忆摘要"));
const searchPlaceholder = computed(() => (currentSection.value === "search" ? "输入关键词按回车进行召回测试…" : "搜索记忆摘要、ID 或作用域…"));

function handleSearchSubmit() {
  appliedSearch.value = searchTerm.value.trim();
  page.value = 1;
}

watch(currentSection, () => {
  page.value = 1;
  searchTerm.value = "";
  appliedSearch.value = "";
});

type RecordObj = Record<string, unknown>;
function asRecord(val: unknown): RecordObj {
  return typeof val === "object" && val !== null && !Array.isArray(val) ? (val as RecordObj) : {};
}
function textAt(record: RecordObj, ...keys: string[]): string {
  for (const k of keys) {
    const val = record[k];
    if (typeof val === "string" && val.trim()) return val;
    if (typeof val === "number") return String(val);
  }
  return "";
}

function badgeTone(stateStr: string): "ok" | "warn" | "error" | "running" | "unknown" {
  const s = stateStr.toLowerCase();
  if (["ready", "ok", "active", "normal", "success", "indexed"].includes(s)) return "ok";
  if (["rebuilding", "running", "syncing", "pending"].includes(s)) return "running";
  if (["warn", "degraded", "stale", "expiring"].includes(s)) return "warn";
  if (["error", "failed", "offline"].includes(s)) return "error";
  return "unknown";
}

const MEMORY_SCOPE_LABELS: Readonly<Record<string, string>> = {
  group: "群聊",
  private: "私聊",
  user: "当前用户",
  global: "全局",
  cross_group: "已授权跨群",
};
const MEMORY_SOURCE_LABELS: Readonly<Record<string, string>> = {
  message: "聊天消息",
  dialogue: "聊天对话",
  curator: "记忆整理",
  manual: "管理员录入",
  imported: "导入记录",
};
const MEMORY_STATUS_LABELS: Readonly<Record<string, string>> = {
  active: "有效",
  expired: "已过期",
  archived: "已归档",
  pending: "待处理",
  inactive: "未启用",
};

function rawMemoryScope(record: RecordObj): string {
  return textAt(record, "scope", "session_type", "group_id");
}

function rawMemorySource(record: RecordObj): string {
  return textAt(record, "source_kind", "source", "type");
}

function rawMemoryStatus(record: RecordObj): string {
  return textAt(record, "status", "state");
}

function knownMemoryLabel(raw: string, labels: Readonly<Record<string, string>>): string {
  return labels[raw.trim().toLowerCase()] || "未知";
}

function memoryScopeLabel(raw: string): string {
  return knownMemoryLabel(raw, MEMORY_SCOPE_LABELS);
}

function memorySourceLabel(raw: string): string {
  return knownMemoryLabel(raw, MEMORY_SOURCE_LABELS);
}

function memoryStatusLabel(raw: string): string {
  return knownMemoryLabel(raw, MEMORY_STATUS_LABELS);
}

function formatScore(score: unknown): string {
  const n = Number(score);
  return Number.isFinite(n) ? n.toFixed(3) : "—";
}

// Recent query
const recentQuery = useQuery<Page<CatalogItem>>({
  queryKey: computed(() => ["memories-page", page.value, appliedSearch.value]),
  queryFn: ({ signal }) => resources.memoryPage(page.value, 20, { search: appliedSearch.value }, signal),
  enabled: computed(() => currentSection.value === "recent"),
});

const memoriesList = computed(() => recentQuery.data.value?.items ?? []);

// Business Query (inner-state, graph, palace-zones, vector-index)
const businessQuery = useQuery<RecordObj>({
  queryKey: computed(() => ["memory-business", currentSection.value]),
  queryFn: ({ signal }) => resources.memoryBusiness(currentSection.value as "recent" | "inner-state" | "graph" | "palace-zones" | "vector-index", signal),
  enabled: computed(() => ["inner-state", "graph", "palace-zones", "vector-index"].includes(currentSection.value)),
});

const businessRecord = computed(() => asRecord(businessQuery.data.value));
const textIndexQuery = useQuery<RecordObj>({ queryKey: ["memory-text-index"], queryFn: ({ signal }) => resources.memoryTextIndex(signal), enabled: computed(() => currentSection.value === "vector-index") });
const embeddingRecord = computed(() => asRecord(businessRecord.value.embedding));
const embeddingState = computed(() => textAt(embeddingRecord.value, "state") || "unknown");
const embeddingConnectivity = computed(() => textAt(embeddingRecord.value, "connectivity_state") || "unknown");
const embeddingIndexed = computed(() => textAt(embeddingRecord.value, "indexed") || "0");
const embeddingTotal = computed(() => textAt(embeddingRecord.value, "total") || "0");
const embeddingPending = computed(() => textAt(embeddingRecord.value, "pending") || "0");
const embeddingRebuilding = computed(() => Boolean(embeddingRecord.value.rebuilding));
const embeddingProvider = computed(() => textAt(embeddingRecord.value, "provider"));
const embeddingModel = computed(() => textAt(embeddingRecord.value, "model"));
const embeddingDimension = computed(() => textAt(embeddingRecord.value, "dimension"));
const embeddingDiagnosticCode = computed(() => textAt(embeddingRecord.value, "diagnostic_code") || "embedding_status_unavailable");
const embeddingStateLabel = computed(() => (({ ready: "索引就绪", rebuilding: "重建中", degraded: "有告警", disabled: "未启用", unknown: "状态未知" } as Record<string, string>)[embeddingState.value] || `未知状态（${embeddingState.value}）`));
const embeddingConnectivityLabel = computed(() => (({ unknown: "未核验", not_applicable: "不适用" } as Record<string, string>)[embeddingConnectivity.value] || `未知状态（${embeddingConnectivity.value}）`));
const embeddingTone = computed(() => embeddingState.value === "ready" ? "ok" : embeddingState.value === "degraded" ? "warn" : "unknown");
const embeddingConnectivityTone = computed(() => embeddingConnectivity.value === "not_applicable" ? "warn" : "unknown");
const graphNodes = computed(() => (Array.isArray(businessRecord.value.nodes) ? (businessRecord.value.nodes as RecordObj[]) : []));
const graphEdges = computed(() => (Array.isArray(businessRecord.value.edges) ? (businessRecord.value.edges as RecordObj[]) : []));
const palaceZones = computed(() => (Array.isArray(businessRecord.value.zones) ? (businessRecord.value.zones as RecordObj[]) : Array.isArray(businessRecord.value.items) ? (businessRecord.value.items as RecordObj[]) : []));
const palaceZoneDetails = computed(() => recordsFromUnknown(businessRecord.value.zone_details));
const palaceZoneCards = computed(() => palaceZoneDetails.value.length ? palaceZoneDetails.value : palaceZones.value);
const selectedPalaceZoneId = ref("");
const zonePage = ref(1);
const selectedPalaceZone = computed(() => palaceZoneCards.value.find((zone) => palaceZoneId(zone) === selectedPalaceZoneId.value) ?? null);
const zoneEntriesQuery = useQuery<Page<CatalogItem>>({
  queryKey: computed(() => ["memory-zone-page", selectedPalaceZoneId.value, zonePage.value]),
  queryFn: ({ signal }) => resources.memoryZonePage(selectedPalaceZoneId.value, zonePage.value, 20, {}, signal),
  enabled: computed(() => currentSection.value === "palace-zones" && Boolean(selectedPalaceZoneId.value)),
});
const selectedZoneEntries = computed(() => zoneEntriesQuery.data.value?.items ?? []);

watch(palaceZoneCards, (zones) => {
  if (selectedPalaceZoneId.value && zones.some((zone) => palaceZoneId(zone) === selectedPalaceZoneId.value)) return;
  selectedPalaceZoneId.value = "";
}, { immediate: true });
watch(selectedPalaceZoneId, () => { zonePage.value = 1; });

function recordsFromUnknown(value: unknown): RecordObj[] {
  return Array.isArray(value)
    ? value.filter((item): item is RecordObj => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

function palaceZoneId(zone: RecordObj): string {
  return textAt(zone, "zone_id", "id", "name");
}

function zoneUpdatedAt(zone: RecordObj): string | number | null {
  const value = zone.last_updated_at ?? zone.updated_at;
  return typeof value === "string" || typeof value === "number" ? value : null;
}

function palaceStatusLabel(value: string): string {
  if (value === "not_configured") return "容量未配置";
  if (value === "ready" || value === "active") return "可用";
  if (value === "unavailable") return "暂不可用";
  return value || "状态未知";
}

// Search query
const searchQuery = useQuery<RecordObj>({
  queryKey: computed(() => ["memory-search", appliedSearch.value, selectedBotId.value]),
  queryFn: ({ signal }) => resources.memorySearch(appliedSearch.value, { platform: "onebot", bot_id: selectedBotId.value }, signal),
  enabled: computed(() => currentSection.value === "search" && Boolean(appliedSearch.value)),
});

const searchResults = computed(() => {
  const d = searchQuery.data.value;
  if (!d) return [];
  if (Array.isArray(d.results)) return d.results as RecordObj[];
  if (Array.isArray(d.items)) return d.items as RecordObj[];
  return [];
});

// Rebuild Mutation
const rebuildFeedback = ref("");
const rebuildSuccess = ref(false);

const rebuildMutation = useMutation({
  mutationFn: () => resources.rebuildMemoryIndex(),
  onSuccess: (data) => {
    rebuildSuccess.value = true;
    rebuildFeedback.value = `重建请求已成功下发（诊断码: ${textAt(asRecord(data), "diagnostic_code", "code") || "rebuild_queued"}）。`;
    businessQuery.refetch();
  },
  onError: (err: Error) => {
    rebuildSuccess.value = false;
    rebuildFeedback.value = `重建提交失败: ${err.message}`;
  },
});

function handleRebuildPrompt() {
  const ok = window.confirm("确认后台重建向量索引？当前已知索引会继续提供读取服务。");
  if (ok) {
    rebuildMutation.mutate();
  }
}
</script>

<style scoped>
.palace-zone-button {
  width: 100%;
}

.palace-card.is-selected {
  border-color: var(--color-accent, currentColor);
  box-shadow: 0 0 0 1px var(--color-accent, currentColor);
}
</style>
