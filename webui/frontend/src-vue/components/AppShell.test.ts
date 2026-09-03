import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { ref, shallowRef } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { BotIdentity } from "@/api/types";
import AppShell from "./AppShell.vue";
import { RUNTIME_EVENTS_KEY, type RuntimeEventsManager } from "@vue-app/realtime/runtimeEvents";
import { useBotStore } from "@vue-app/stores/bot";

vi.mock("@/api/resources", () => ({ resources: { bots: vi.fn() } }));

const bots: BotIdentity[] = [
  { bot_id: "10001", nickname: "测试主号", avatar_url: null, online: true, is_default: true, last_seen_at: 1 },
  { bot_id: "10002", nickname: "测试副号", avatar_url: null, online: false, is_default: false, last_seen_at: null },
];

function runtimeManager(): RuntimeEventsManager {
  return {
    events: shallowRef([]),
    state: ref("open"),
    resyncCount: ref(0),
    client: shallowRef(null),
    start: vi.fn(),
    stop: vi.fn(),
  };
}

describe("AppShell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.mocked(resources.bots).mockResolvedValue({ items: bots, total: 2, diagnostic_code: "ok" });
  });

  async function renderShell() {
    const pinia = createPinia();
    setActivePinia(pinia);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/runtime/overview/summary", component: { template: "<div />" } },
        { path: "/runtime/agent/status", component: { template: "<div />" } },
      ],
    });
    await router.push("/runtime/overview/summary");
    await router.isReady();
    const wrapper = mount(AppShell, {
      slots: { default: "<div id='test-content'>测试内容</div>" },
      global: {
        plugins: [pinia, [VueQueryPlugin, { queryClient }], router],
        provide: { [RUNTIME_EVENTS_KEY as symbol]: runtimeManager() },
      },
    });
    return { wrapper, router, queryClient };
  }

  it("提供跳转、旧版入口和可折叠导航", async () => {
    const { wrapper, queryClient } = await renderShell();
    expect(wrapper.get(".skip-link").text()).toBe("跳到主要内容");
    expect(wrapper.get(".legacy-entry").attributes("href")).toBe("/personification/");
    expect(wrapper.get("#test-content").text()).toBe("测试内容");
    await wrapper.get(".rail-collapse").trigger("click");
    expect(wrapper.get(".app-frame").classes()).toContain("rail-collapsed");
    expect(window.localStorage.getItem("personification.nav.collapsed")).toBe("1");
    wrapper.unmount();
    queryClient.clear();
  });

  it("无真实网络即可选择 Bot、搜索页面和控制移动抽屉", async () => {
    const { wrapper, queryClient } = await renderShell();
    await vi.waitFor(() => expect(wrapper.find(".bot-selector").exists()).toBe(true));
    await wrapper.get(".bot-selector").setValue("10002");
    expect(useBotStore().selectedBotId).toBe("10002");

    await wrapper.get(".global-page-search input").setValue("告警");
    expect(wrapper.findAll(".page-search-results button").length).toBeGreaterThan(0);

    await wrapper.get(".mobile-nav-trigger").trigger("click");
    expect(wrapper.get(".evidence-rail").classes()).toContain("is-open");
    await wrapper.get(".drawer-scrim").trigger("click");
    expect(wrapper.get(".evidence-rail").classes()).not.toContain("is-open");
    wrapper.unmount();
    queryClient.clear();
  });
});
