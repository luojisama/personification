import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";

import CurrentGroupSelect from "./CurrentGroupSelect.vue";
import { resources } from "@/api/resources";
import { useBotStore } from "@vue-app/stores/bot";
import { useCurrentGroupStore } from "@vue-app/stores/currentGroup";

vi.mock("@/api/resources", () => ({
  resources: {
    groupsFiltered: vi.fn(),
  },
}));

describe("CurrentGroupSelect.vue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("URL 群参数优先，并在切换 Bot 时恢复该 Bot 的群选择和地址栏", async () => {
    vi.mocked(resources.groupsFiltered).mockImplementation(async (_page, _size, filters) => {
      const botId = filters.bot_id;
      const groupId = botId === "bot-b" ? "20002" : "10001";
      return {
        items: [{
          group_id: groupId,
          group_name: botId === "bot-b" ? "乙群" : "甲群",
          avatar_url: null,
          enabled: true,
          membership_state: "confirmed",
          bot_ids: [botId || "bot-a"],
          sources: [],
          bot_self_ids: [botId || "bot-a"],
          member_count: null,
          last_active_at: null,
          freshness: 1,
          cache_only: false,
        }],
        page: 1,
        page_size: 100,
        total: 1,
        total_pages: 1,
      };
    });

    const pinia = createPinia();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    setActivePinia(pinia);
    const botStore = useBotStore();
    const groupStore = useCurrentGroupStore();
    botStore.setBotId("bot-a");
    groupStore.setGroupId("bot-a", "stale-group");
    groupStore.setGroupId("bot-b", "20002");

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/groups", component: CurrentGroupSelect }],
    });
    await router.push({ path: "/groups", query: { group_id: "10001" } });
    await router.isReady();

    mount(CurrentGroupSelect, {
      props: { allowEmpty: true },
      global: { plugins: [pinia, router, [VueQueryPlugin, { queryClient }]] },
    });
    await flushPromises();

    expect(groupStore.groupIdFor("bot-a")).toBe("10001");
    expect(router.currentRoute.value.query.group_id).toBe("10001");

    botStore.setBotId("bot-b");
    await flushPromises();
    expect(groupStore.groupIdFor("bot-b")).toBe("20002");
    expect(router.currentRoute.value.query.group_id).toBe("20002");
  });
});
