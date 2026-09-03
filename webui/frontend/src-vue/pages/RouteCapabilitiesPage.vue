<template>
  <div class="page-stack">
    <PageHeader
      index="06"
      title="路由能力"
      description="能力绑定 Provider、API 类型、URL 指纹、模型和媒体协议。超时与上游故障保持“未知”，不会伪装成“不支持”。"
    >
      <template #actions>
        <div class="search-field">
          <input
            v-model="searchTerm"
            type="search"
            placeholder="搜索 Provider、模型或指纹"
            aria-label="搜索路由能力"
            @input="onSearchInput"
          />
        </div>
      </template>
    </PageHeader>

    <nav class="tabs" aria-label="路由能力导航">
      <div class="tab-list" role="tablist">
        <RouterLink
          to="/runtime/routes/capabilities"
          role="tab"
          :class="['tab-item', { active: currentSection === 'capabilities' || !currentSection }]"
          :aria-selected="currentSection === 'capabilities' || !currentSection"
        >
          能力列表
        </RouterLink>
        <RouterLink
          to="/runtime/routes/probes"
          role="tab"
          :class="['tab-item', { active: currentSection === 'probes' }]"
          :aria-selected="currentSection === 'probes'"
        >
          探针状态
        </RouterLink>
        <RouterLink
          to="/runtime/routes/video"
          role="tab"
          :class="['tab-item', { active: currentSection === 'video' }]"
          :aria-selected="currentSection === 'video'"
        >
          视频协议与证据
        </RouterLink>
      </div>
    </nav>

    <QueryBoundary :pending="isPending" :error="error">
      <template v-if="data">
        <EmptyState v-if="data.items.length === 0" code="route_capability_list_empty">
          没有匹配的路由能力记录。
        </EmptyState>
        <template v-else>
          <div class="route-dossier-list">
            <Panel
              v-for="route in data.items"
              :key="route.route_fingerprint"
              as="article"
              class="route-dossier"
              :eyebrow="`${route.provider} / ${route.api_type}`"
              :title="route.model"
            >
              <template #actions>
                <button
                  class="button button-secondary"
                  type="button"
                  :disabled="probingMap[route.route_fingerprint] || isPendingProbe"
                  @click="triggerProbe(route.route_fingerprint)"
                >
                  <Icon name="refresh" />
                  {{ probingMap[route.route_fingerprint] ? '正在排队' : '视觉重测' }}
                </button>
              </template>

              <div class="route-meta-line">
                <code :title="route.route_fingerprint">{{ shortId(route.route_fingerprint, 10) }}</code>
                <span>{{ route.media_protocol || '未声明媒体协议' }}</span>
                <StateBadge
                  :tone="route.probe_status === 'running' || route.probe_status === 'queued' ? 'running' : 'unknown'"
                  :raw="route.probe_status"
                >
                  {{ route.probe_status === 'running' ? '探针运行中' : route.probe_status === 'queued' ? '探针已排队' : '使用缓存证据' }}
                </StateBadge>
              </div>

              <div class="capability-grid">
                <div
                  v-for="(cap, name) in route.capabilities"
                  :key="name"
                  class="capability-cell"
                >
                  <StateBadge
                    :tone="cap.state === 'supported' ? 'ok' : cap.state === 'unsupported' ? 'error' : 'unknown'"
                    :title="`${capabilityStateLabel(cap.state)} · ${capabilitySourceLabel(cap.source)} · ${cap.detail_code}`"
                  >
                    {{ CAPABILITY_LABELS[name as CapabilityName] || name }}: {{ capabilityStateLabel(cap.state) }}
                  </StateBadge>
                  <dl>
                    <div><dt>证据</dt><dd>{{ capabilitySourceLabel(cap.source) }}</dd></div>
                    <div><dt>验证</dt><dd>{{ formatDateTime(cap.checked_at) }}</dd></div>
                    <div><dt>诊断</dt><dd><code>{{ cap.detail_code }}</code></dd></div>
                  </dl>
                </div>
              </div>

              <footer class="route-summary">
                <span>支持 {{ countCapabilities(route.capabilities, 'supported') }}</span>
                <span>未知 {{ countCapabilities(route.capabilities, 'unknown') }}</span>
                <span>不支持 {{ countCapabilities(route.capabilities, 'unsupported') }}</span>
              </footer>
            </Panel>
          </div>

          <nav v-if="data.total_pages > 1" class="pagination" aria-label="分页导航">
            <button
              type="button"
              :disabled="page <= 1"
              aria-label="上一页"
              @click="page = Math.max(1, page - 1)"
            >
              ‹
            </button>
            <span>第 {{ data.page }} / {{ data.total_pages }} 页</span>
            <button
              type="button"
              :disabled="page >= data.total_pages"
              aria-label="下一页"
              @click="page = Math.min(data.total_pages, page + 1)"
            >
              ›
            </button>
          </nav>
        </template>
      </template>
    </QueryBoundary>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";

import { resources } from "@/api/resources";
import type { CapabilityName, RouteCapabilities } from "@/api/types";
import { formatDateTime, shortId } from "@/lib/format";
import { capabilitySourceLabel, capabilityStateLabel } from "@/lib/labels";
import EmptyState from "@vue-app/components/EmptyState.vue";
import Icon from "@vue-app/components/Icon.vue";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";

const CAPABILITY_LABELS: Record<CapabilityName, string> = {
  image_input: "图片",
  audio_input: "音频",
  video_input: "视频",
  reasoning: "推理",
  function_call: "函数",
  native_web_search: "原生搜索",
  external_network_access: "Agent 外网",
};

const route = useRoute();
const queryClient = useQueryClient();
const currentSection = computed(() => String(route.params.section || "capabilities"));

const page = ref(1);
const searchTerm = ref("");
const probingMap = reactive<Record<string, boolean>>({});

const { data, isPending, error } = useQuery({
  queryKey: computed(() => ["route-capabilities", page.value, searchTerm.value]),
  queryFn: ({ signal }) => resources.routes(page.value, 20, searchTerm.value, signal),
});

const { mutate: mutateProbe, isPending: isPendingProbe } = useMutation({
  mutationFn: (fingerprint: string) => resources.queueRouteProbe(fingerprint),
  onSuccess: (_, fingerprint) => {
    probingMap[fingerprint] = false;
    void queryClient.invalidateQueries({ queryKey: ["route-capabilities"] });
  },
  onError: (_, fingerprint) => {
    probingMap[fingerprint] = false;
  },
});

function triggerProbe(fingerprint: string) {
  probingMap[fingerprint] = true;
  mutateProbe(fingerprint);
}

function onSearchInput() {
  page.value = 1;
}

function countCapabilities(capabilities: RouteCapabilities, state: "supported" | "unsupported" | "unknown"): number {
  return Object.values(capabilities).filter((cap) => cap.state === state).length;
}
</script>
