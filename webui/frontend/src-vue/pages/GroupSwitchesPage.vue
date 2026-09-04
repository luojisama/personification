<template>
  <div class="page-stack">
    <PageHeader
      index="群开关"
      title="群功能开关"
      description="按页读取本地群目录，明确显示群头像、群号、群名、配置来源与启用状态。未确认群候选默认不进入列表。"
    >
      <template #actions>
        <TextField
          v-model="searchDraft"
          class="search-field"
          label="搜索群号或群名"
          hide-label
          type="search"
          placeholder="搜索群号或群名"
          @update:model-value="handleSearchInput"
        />
      </template>
    </PageHeader>

    <Panel eyebrow="FILTER / GROUP SWITCHES" title="开关筛选">
      <div class="inline-controls filter-control-row">
        <SelectField
          :model-value="enabled"
          label="按启用状态筛选"
          hide-label
          :options="enabledOptions"
          @update:model-value="onEnabledChange"
        />
        <StateBadge tone="unknown">当前 Bot {{ botStore.selectedBotId || "全部" }}</StateBadge>
        <span v-if="query.data.value">
          启用 {{ query.data.value.enabled_total }} / 停用 {{ query.data.value.disabled_total }}
        </span>
      </div>
    </Panel>

    <QueryBoundary :pending="query.isPending.value" :error="query.error.value">
      <Panel
        v-if="query.data.value?.items?.length"
        eyebrow="GROUPS / SWITCHES"
        :title="`群开关（本页 ${query.data.value.items.length} 项）`"
      >
        <div class="trace-table-wrap">
          <table class="forensic-table">
            <thead>
              <tr>
                <th>群</th>
                <th>群号</th>
                <th>状态</th>
                <th>来源</th>
                <th>关系</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in query.data.value.items" :key="item.group_id">
                <td>
                  <div class="table-identity">
                    <span class="avatar-stamp">
                      <img
                        v-if="item.avatar_url"
                        :src="item.avatar_url"
                        :alt="item.group_name || item.group_id"
                        referrerpolicy="no-referrer"
                      />
                      <span v-else>{{ (item.group_name || item.group_id).slice(0, 1) }}</span>
                    </span>
                    <strong>{{ item.group_name || "未缓存群名" }}</strong>
                  </div>
                </td>
                <td><code>{{ item.group_id }}</code></td>
                <td>
                  <StateBadge :tone="item.enabled ? 'ok' : 'error'">
                    {{ item.enabled ? "启用" : "停用" }}
                  </StateBadge>
                </td>
                <td>
                  <StateBadge tone="unknown" :raw="item.source">
                    {{ sourceLabel(item.source) }}
                  </StateBadge>
                  <small v-if="item.static_config_readonly"> 静态项由群配置覆盖</small>
                </td>
                <td>
                  <StateBadge :tone="item.membership_state === 'confirmed' ? 'ok' : 'unknown'">
                    {{ item.membership_state === "confirmed" ? "已确认" : "已配置" }}
                  </StateBadge>
                </td>
                <td>
                  <button
                    :class="item.enabled ? 'button button-danger' : 'button'"
                    type="button"
                    :disabled="pendingGroupId === item.group_id"
                    @click="requestChange(item)"
                  >
                    {{ pendingGroupId === item.group_id ? "保存并核对…" : item.enabled ? "停用" : "启用" }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel v-else eyebrow="EMPTY" title="暂无数据">
        <p class="muted-copy">当前筛选条件下没有已确认或已配置的群。</p>
      </Panel>
    </QueryBoundary>

    <div v-if="query.data.value && query.data.value.total_pages > 1" class="pagination">
      <button type="button" :disabled="page <= 1" @click="setPage(page - 1)">上一页</button>
      <span>第 {{ query.data.value.page }} / {{ query.data.value.total_pages }} 页（共 {{ query.data.value.total }} 条）</span>
      <button type="button" :disabled="page >= query.data.value.total_pages" @click="setPage(page + 1)">下一页</button>
    </div>

    <Panel v-if="lastDiagnostic" eyebrow="DIAGNOSTIC / RESULT" :title="lastDiagnostic.title || '操作结果'">
      <div class="diagnostic-summary">
        <StateBadge :tone="lastDiagnostic.ok ? 'ok' : 'error'">{{ lastDiagnostic.ok ? "成功" : "失败" }}</StateBadge>
        <span><code>{{ lastDiagnostic.code }}</code> {{ lastDiagnostic.message }}</span>
      </div>
    </Panel>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, useRouter } from "vue-router";

import { resources } from "@/api/resources";
import { diagnosticFromError } from "@/api/diagnostics";
import type { GroupSwitchItem, OperationDiagnostic } from "@/api/types";
import PageHeader from "@vue-app/components/PageHeader.vue";
import Panel from "@vue-app/components/Panel.vue";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";
import StateBadge from "@vue-app/components/StateBadge.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";
import { useBotStore } from "@vue-app/stores/bot";

const route = useRoute();
const router = useRouter();
const botStore = useBotStore();
const queryClient = useQueryClient();

const page = computed(() => Math.max(1, Number(route.query.page ?? 1) || 1));
const search = computed(() => String(route.query.search ?? ""));
const enabled = computed(() => String(route.query.enabled ?? ""));
const enabledOptions = [
  { value: "", label: "全部状态" },
  { value: "true", label: "仅已启用" },
  { value: "false", label: "仅已停用" },
];

const searchDraft = ref(search.value);
watch(search, (next) => { searchDraft.value = next; });

let debounceTimer: ReturnType<typeof setTimeout> | undefined;
function handleSearchInput() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    const nextQuery = { ...route.query, search: searchDraft.value || undefined, page: "1" };
    router.replace({ query: nextQuery });
  }, 300);
}

function onEnabledChange(value: string) {
  router.replace({ query: { ...route.query, enabled: value || undefined, page: "1" } });
}

function setPage(target: number) {
  router.replace({ query: { ...route.query, page: String(target) } });
}

function sourceLabel(value: GroupSwitchItem["source"]): string {
  if (value === "group_config") return "群配置";
  if (value === "config_file") return "静态配置";
  if (value === "dynamic") return "动态白名单";
  return "未配置";
}

const pendingGroupId = ref("");
const lastDiagnostic = ref<OperationDiagnostic | null>(null);

const query = useQuery({
  queryKey: computed(() => ["group-switches", page.value, search.value, enabled.value, botStore.selectedBotId]),
  queryFn: ({ signal }) => resources.groupSwitches(page.value, 20, { search: search.value, enabled: enabled.value, bot_id: botStore.selectedBotId }, signal),
});

const switchMutation = useMutation({
  mutationFn: ({ groupId, target }: { groupId: string; target: boolean }) => resources.updateGroupSwitch(groupId, target),
  onMutate: ({ groupId }) => { pendingGroupId.value = groupId; },
  onSuccess: (diagnostic) => {
    lastDiagnostic.value = diagnostic;
    void queryClient.invalidateQueries({ queryKey: ["group-switches"] });
  },
  onError: (error) => {
    lastDiagnostic.value = diagnosticFromError(error);
  },
  onSettled: () => { pendingGroupId.value = ""; },
});

function requestChange(item: GroupSwitchItem) {
  const target = !item.enabled;
  const action = target ? "启用" : "停用";
  if (!window.confirm(`确认${action}群 ${item.group_name || item.group_id}（${item.group_id}）？\n操作将写入 group_config.enabled，并在保存后重新读取确认。`)) return;
  switchMutation.mutate({ groupId: item.group_id, target });
}
</script>
