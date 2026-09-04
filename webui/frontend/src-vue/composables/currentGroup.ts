import { computed, watch } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { useRoute, useRouter } from "vue-router";

import { resources } from "@/api/resources";
import type { GroupListItem } from "@/api/types";
import { useBotStore } from "@vue-app/stores/bot";
import { useCurrentGroupStore } from "@vue-app/stores/currentGroup";

export interface CurrentGroupOption {
  value: string;
  label: string;
  description?: string;
}

export function groupIdFromQuery(value: unknown): string {
  const candidate = Array.isArray(value) ? value[0] : value;
  return typeof candidate === "string" ? candidate.trim() : "";
}

export function resolveCurrentGroupId(queryGroupId: unknown, storedGroupId: string): string {
  return groupIdFromQuery(queryGroupId) || String(storedGroupId || "").trim();
}

export function groupOptionsFromItems(
  items: readonly GroupListItem[],
  options: { allowEmpty?: boolean; emptyLabel?: string } = {},
): CurrentGroupOption[] {
  const result: CurrentGroupOption[] = [];
  if (options.allowEmpty) {
    result.push({ value: "", label: options.emptyLabel || "不限定群" });
  }
  for (const item of items) {
    const groupId = String(item.group_id || "").trim();
    if (!groupId) continue;
    const groupName = String(item.group_name || "").trim();
    result.push({
      value: groupId,
      label: groupName ? `${groupName} · ${groupId}` : groupId,
      description: item.enabled ? "已启用" : "已停用",
    });
  }
  return result;
}

export function useCurrentGroupSelection(options: { allowEmpty?: boolean; emptyLabel?: string } = {}) {
  const route = useRoute();
  const router = useRouter();
  const botStore = useBotStore();
  const groupStore = useCurrentGroupStore();
  const botId = computed(() => String(botStore.selectedBotId || "").trim());
  const routeGroupId = computed(() => groupIdFromQuery(route.query.group_id));

  const groupsQuery = useQuery({
    queryKey: computed(() => ["current-bot-groups", botId.value]),
    queryFn: async ({ signal, queryKey }) => {
      const requestedBotId = String(queryKey[1] || "");
      const page = await resources.groupsFiltered(
        1,
        100,
        {
          bot_id: requestedBotId,
          include_unconfirmed: false,
          sort_by: "group_id",
          direction: "asc",
        },
        signal,
      );
      return { botId: requestedBotId, page };
    },
    enabled: computed(() => Boolean(botId.value)),
    staleTime: 30_000,
  });

  const currentGroupPage = computed(() => groupsQuery.data.value?.botId === botId.value
    ? groupsQuery.data.value.page
    : null);
  const groupItems = computed<GroupListItem[]>(() => currentGroupPage.value?.items ?? []);
  const groupOptions = computed(() => groupOptionsFromItems(groupItems.value, options));
  const hasCompleteGroupList = computed(() => {
    const data = currentGroupPage.value;
    return Boolean(data && data.total <= data.items.length);
  });
  const selectedGroupId = computed(() => resolveCurrentGroupId(routeGroupId.value, groupStore.groupIdFor(botId.value)));

  function replaceRouteGroupId(groupId: string): void {
    const next = { ...route.query };
    const safeGroupId = String(groupId || "").trim();
    if (safeGroupId) next.group_id = safeGroupId;
    else delete next.group_id;
    void router.replace({ query: next });
  }

  function selectGroup(groupId: string): void {
    const safeGroupId = String(groupId || "").trim();
    if (botId.value) groupStore.setGroupId(botId.value, safeGroupId);
    replaceRouteGroupId(safeGroupId);
  }

  let initialized = false;
  let lastBotId = "";
  let pendingBotGroupRestore: string | null = null;
  watch(botId, (nextBotId) => {
    if (!nextBotId) return;
    if (!initialized) {
      initialized = true;
      lastBotId = nextBotId;
      if (routeGroupId.value) groupStore.setGroupId(nextBotId, routeGroupId.value);
      else if (groupStore.groupIdFor(nextBotId)) {
        pendingBotGroupRestore = groupStore.groupIdFor(nextBotId);
        replaceRouteGroupId(pendingBotGroupRestore);
      }
      return;
    }
    if (nextBotId !== lastBotId) {
      lastBotId = nextBotId;
      pendingBotGroupRestore = groupStore.groupIdFor(nextBotId);
      replaceRouteGroupId(pendingBotGroupRestore);
    }
  }, { immediate: true });

  watch(routeGroupId, (nextGroupId) => {
    if (pendingBotGroupRestore !== null && nextGroupId === pendingBotGroupRestore) {
      pendingBotGroupRestore = null;
    }
    if (botId.value && nextGroupId) groupStore.setGroupId(botId.value, nextGroupId);
  });

  watch(currentGroupPage, (page) => {
    if (!botId.value || !page || !hasCompleteGroupList.value || pendingBotGroupRestore !== null) return;
    const current = selectedGroupId.value;
    if (!current) return;
    const validIds = page.items.map((item) => String(item.group_id || "")).filter(Boolean);
    if (validIds.includes(current)) return;
    groupStore.reconcileGroups(botId.value, validIds, true);
    if (routeGroupId.value === current) replaceRouteGroupId("");
  }, { immediate: true });

  return {
    botId,
    selectedGroupId,
    groupOptions,
    groupsQuery,
    selectGroup,
  };
}
