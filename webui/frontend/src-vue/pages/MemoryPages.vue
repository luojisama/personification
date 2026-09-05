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
                  <strong>{{ textAt(row, 'summary', 'content_summary', 'text') || '（无摘要）' }}</strong>
                  <br />
                  <code>{{ textAt(row, 'memory_id', 'id') || '—' }}</code>
                </td>
                <td>{{ textAt(row, 'scope', 'session_type', 'group_id') || '全局' }}</td>
                <td>{{ textAt(row, 'source_kind', 'source', 'type') || '对话' }}</td>
                <td>
                  <StateBadge :tone="badgeTone(textAt(row, 'status', 'state'))">
                    {{ textAt(row, 'status', 'state') || '正常' }}
                  </StateBadge>
                </td>
                <td>{{ formatDateTime(row.expires_at as string | number | null) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="recentQuery.data.value && recentQuery.data.value.total_pages > 1" class="pagination">
          <button
            type="button"
            :disabled="page <= 1"
            aria-label="上一页"
            @click="page--"
          >
            上一页
          </button>
          <span>第 {{ page }} / {{ recentQuery.data.value.total_pages }} 页</span>
          <button
            type="button"
            :disabled="page >= recentQuery.data.value.total_pages"
            aria-label="下一页"
            @click="page++"
          >
            下一页
          </button>
        </div>
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
              <StateBadge :tone="badgeTone(textAt(item, 'status'))">
                {{ textAt(item, 'status') || '已索引' }}
              </StateBadge>
            </header>
            <p class="result-summary">{{ textAt(item, 'summary', 'content', 'text') || '—' }}</p>
            <footer class="result-meta">
              <code>{{ textAt(item, 'memory_id', 'id') }}</code>
              <span>作用域: {{ textAt(item, 'scope', 'group_id') || '通用' }}</span>
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
            <strong>{{ textAt(businessRecord, 'mood', 'emotion') || '平静' }}</strong>
            <small>能级: {{ textAt(businessRecord, 'energy', 'vitality') || '标准' }}</small>
          </article>
          <article class="state-metric-card">
            <span class="metric-label">工作记忆负荷</span>
            <strong>{{ textAt(businessRecord, 'working_memory_count', 'load') || '0' }} 项</strong>
            <small>待巩固项目: {{ textAt(businessRecord, 'pending_consolidation', 'pending_count') || '0' }}</small>
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

          <div v-if="selectedZoneEntriesProvided && selectedZoneEntries.length" class="table-responsive">
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
                    <strong>{{ textAt(entry, 'summary', 'title', 'label') || '（未提供摘要）' }}</strong>
                    <br />
                    <code>{{ textAt(entry, 'memory_id', 'id') || '—' }}</code>
                  </td>
                  <td>{{ textAt(entry, 'status', 'state') || '—' }}</td>
                  <td>{{ formatDateTime(entry.updated_at as string | number | null) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else-if="selectedZoneEntriesProvided" class="empty-notice">当前分区暂无可展示条目。</p>
          <p v-else class="empty-notice">后端暂未提供该分区的条目明细；页面不会根据汇总数量生成虚拟条目。</p>
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
                {{ textAt(businessRecord, 'status', 'state') || '就绪' }}
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
            <dd><code>{{ textAt(businessRecord, 'diagnostic_code', 'code') || 'vector_index_ready' }}</code></dd>
          </div>
        </dl>

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

function formatScore(score: unknown): string {
  const n = Number(score);
  return Number.isFinite(n) ? n.toFixed(3) : "—";
}

// Recent query
const recentQuery = useQuery<Page<CatalogItem>>({
  queryKey: computed(() => ["memories-catalog", page.value, appliedSearch.value]),
  queryFn: ({ signal }) => resources.catalog("memories", page.value, 20, appliedSearch.value, signal),
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
const graphNodes = computed(() => (Array.isArray(businessRecord.value.nodes) ? (businessRecord.value.nodes as RecordObj[]) : []));
const graphEdges = computed(() => (Array.isArray(businessRecord.value.edges) ? (businessRecord.value.edges as RecordObj[]) : []));
const palaceZones = computed(() => (Array.isArray(businessRecord.value.zones) ? (businessRecord.value.zones as RecordObj[]) : Array.isArray(businessRecord.value.items) ? (businessRecord.value.items as RecordObj[]) : []));
const palaceZoneDetails = computed(() => recordsFromUnknown(businessRecord.value.zone_details));
const palaceZoneCards = computed(() => palaceZoneDetails.value.length ? palaceZoneDetails.value : palaceZones.value);
const selectedPalaceZoneId = ref("");
const selectedPalaceZone = computed(() => palaceZoneCards.value.find((zone) => palaceZoneId(zone) === selectedPalaceZoneId.value) ?? null);
const selectedZoneEntriesProvided = computed(() => Boolean(selectedPalaceZone.value && Array.isArray(selectedPalaceZone.value.entries)));
const selectedZoneEntries = computed(() => selectedPalaceZone.value ? recordsFromUnknown(selectedPalaceZone.value.entries) : []);

watch(palaceZoneCards, (zones) => {
  if (selectedPalaceZoneId.value && zones.some((zone) => palaceZoneId(zone) === selectedPalaceZoneId.value)) return;
  selectedPalaceZoneId.value = "";
}, { immediate: true });

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
  queryKey: computed(() => ["memory-search", appliedSearch.value]),
  queryFn: ({ signal }) => resources.memorySearch(appliedSearch.value, signal),
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
