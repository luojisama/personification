import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import {
  CURRENT_GROUP_STORAGE_KEY,
  groupIdForBot,
  reconcileCurrentGroup,
  useCurrentGroupStore,
} from "./currentGroup";
import { groupIdFromQuery, resolveCurrentGroupId } from "@vue-app/composables/currentGroup";

describe("current group store", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setActivePinia(createPinia());
  });

  it("按 Bot 分桶保存群选择，切换 Bot 后能恢复各自选择", () => {
    const store = useCurrentGroupStore();
    store.setGroupId("bot-a", "10001");
    store.setGroupId("bot-b", "20002");

    expect(store.groupIdFor("bot-a")).toBe("10001");
    expect(store.groupIdFor("bot-b")).toBe("20002");
    expect(JSON.parse(window.localStorage.getItem(CURRENT_GROUP_STORAGE_KEY) || "{}")).toEqual({
      "bot-a": "10001",
      "bot-b": "20002",
    });
  });

  it("只在已拿到完整群目录时清理无效选择", () => {
    const store = useCurrentGroupStore();
    store.setGroupId("bot-a", "10001");
    expect(store.reconcileGroups("bot-a", ["20002"], false)).toBe("10001");
    expect(store.reconcileGroups("bot-a", ["20002"], true)).toBe("");
    expect(store.groupIdFor("bot-a")).toBe("");

    expect(reconcileCurrentGroup({ "bot-b": "30003" }, "bot-b", ["30003"], true)).toEqual({ "bot-b": "30003" });
    expect(groupIdForBot({ "bot-b": "30003" }, "bot-b")).toBe("30003");
  });

  it("URL 的显式 group_id 优先，并安全读取重复 query", () => {
    expect(groupIdFromQuery(["40004", "ignored"])).toBe("40004");
    expect(resolveCurrentGroupId("50005", "10001")).toBe("50005");
    expect(resolveCurrentGroupId(undefined, "10001")).toBe("10001");
  });
});
