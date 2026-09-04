import { defineStore } from "pinia";
import { ref } from "vue";

export const CURRENT_GROUP_STORAGE_KEY = "personification:selected-groups-by-bot";

export type CurrentGroupsByBot = Record<string, string>;

function normalizeGroups(value: unknown): CurrentGroupsByBot {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const normalized: CurrentGroupsByBot = {};
  for (const [botId, groupId] of Object.entries(value)) {
    const safeBotId = String(botId || "").trim();
    const safeGroupId = typeof groupId === "string" ? groupId.trim() : "";
    if (safeBotId && safeGroupId) normalized[safeBotId] = safeGroupId;
  }
  return normalized;
}

function readStoredGroups(): CurrentGroupsByBot {
  try {
    const raw = window.localStorage.getItem(CURRENT_GROUP_STORAGE_KEY);
    return raw ? normalizeGroups(JSON.parse(raw)) : {};
  } catch {
    return {};
  }
}

function writeStoredGroups(value: CurrentGroupsByBot): void {
  try {
    if (Object.keys(value).length) {
      window.localStorage.setItem(CURRENT_GROUP_STORAGE_KEY, JSON.stringify(value));
    } else {
      window.localStorage.removeItem(CURRENT_GROUP_STORAGE_KEY);
    }
  } catch {
    // 本地存储不可用时仍保留当前页面内的 Bot/群映射。
  }
}

export function groupIdForBot(groups: CurrentGroupsByBot, botId: string): string {
  return groups[String(botId || "").trim()] ?? "";
}

export function reconcileCurrentGroup(
  groups: CurrentGroupsByBot,
  botId: string,
  validGroupIds: readonly string[],
  complete: boolean,
): CurrentGroupsByBot {
  const safeBotId = String(botId || "").trim();
  const currentGroupId = groupIdForBot(groups, safeBotId);
  if (!safeBotId || !currentGroupId || !complete || validGroupIds.includes(currentGroupId)) {
    return groups;
  }
  const next = { ...groups };
  delete next[safeBotId];
  return next;
}

export const useCurrentGroupStore = defineStore("current-group", () => {
  const selectedGroupIds = ref<CurrentGroupsByBot>(readStoredGroups());

  function groupIdFor(botId: string): string {
    return groupIdForBot(selectedGroupIds.value, botId);
  }

  function setGroupId(botId: string, groupId: string): void {
    const safeBotId = String(botId || "").trim();
    if (!safeBotId) return;
    const safeGroupId = String(groupId || "").trim();
    const next = { ...selectedGroupIds.value };
    if (safeGroupId) next[safeBotId] = safeGroupId;
    else delete next[safeBotId];
    selectedGroupIds.value = next;
    writeStoredGroups(next);
  }

  function reconcileGroups(botId: string, validGroupIds: readonly string[], complete: boolean): string {
    const next = reconcileCurrentGroup(selectedGroupIds.value, botId, validGroupIds, complete);
    if (next !== selectedGroupIds.value) {
      selectedGroupIds.value = next;
      writeStoredGroups(next);
    }
    return groupIdForBot(next, botId);
  }

  return { selectedGroupIds, groupIdFor, setGroupId, reconcileGroups };
});
