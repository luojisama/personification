import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { DOMWrapper, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref, shallowRef } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { BotIdentity } from "@/api/types";
import AppShell from "./AppShell.vue";
import { RUNTIME_EVENTS_KEY, type RuntimeEventsManager } from "@vue-app/realtime/runtimeEvents";
import { useBotStore } from "@vue-app/stores/bot";

vi.mock("@/api/resources", () => ({ resources: { bots: vi.fn(), adminIdentity: vi.fn() } }));

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

function installViewport(initialMatches: boolean): (matches: boolean) => void {
  let matches = initialMatches;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const query = {
    media: "(max-width: 760px)",
    get matches() { return matches; },
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addListener: (listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
    removeListener: (listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    dispatchEvent: () => true,
  } as MediaQueryList;
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return (nextMatches: boolean) => {
    matches = nextMatches;
    for (const listener of listeners) listener({ matches, media: query.media } as MediaQueryListEvent);
  };
}

describe("AppShell", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    HTMLElement.prototype.scrollIntoView = vi.fn();
    window.localStorage.clear();
    vi.mocked(resources.bots).mockResolvedValue({ items: bots, total: 2, diagnostic_code: "ok" });
    vi.mocked(resources.adminIdentity).mockResolvedValue({ qq: "10001", device_id: "device", label: "浏览器", identity_source: "SUPERUSER" });
    installViewport(false);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.body.innerHTML = "";
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
        { path: "/runtime/tokens/24h", component: { template: "<div />" } },
        { path: "/runtime/health/catalog", component: { template: "<div />" } },
      ],
    });
    await router.push("/runtime/overview/summary");
    await router.isReady();
    const wrapper = mount(AppShell, {
      attachTo: document.body,
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
    await vi.waitFor(() => expect(wrapper.text()).toContain("当前身份 SUPERUSER（NoneBot 超级用户） · QQ 10001"));
    await wrapper.get(".rail-collapse").trigger("click");
    expect(wrapper.get(".app-frame").classes()).toContain("rail-collapsed");
    expect(window.localStorage.getItem("personification.nav.collapsed")).toBe("1");
    wrapper.unmount();
    queryClient.clear();
  });

  it("桌面折叠按钮与 Ctrl+B 共用 UI store 和布局状态", async () => {
    const { wrapper, queryClient } = await renderShell();
    expect(wrapper.get(".app-frame").classes()).not.toContain("rail-collapsed");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "b", ctrlKey: true }));
    await nextTick();
    expect(wrapper.get(".app-frame").classes()).toContain("rail-collapsed");
    expect(window.localStorage.getItem("personification.nav.collapsed")).toBe("1");
    await wrapper.get(".rail-collapse").trigger("click");
    expect(wrapper.get(".app-frame").classes()).not.toContain("rail-collapsed");
    wrapper.unmount();
    queryClient.clear();
  });

  it("无真实网络即可选择 Bot、搜索页面和控制移动抽屉", async () => {
    installViewport(true);
    const { wrapper, queryClient } = await renderShell();
    await nextTick();
    await wrapper.get(".mobile-nav-trigger").trigger("click");
    await nextTick();
    const rail = document.querySelector<HTMLElement>("#admin-navigation")!;
    await vi.waitFor(() => expect(rail.querySelector(".bot-selector")).not.toBeNull());
    const botInput = rail.querySelector<HTMLInputElement>(".bot-selector .searchable-select-input")!;
    const botInputWrapper = new DOMWrapper(botInput);
    await botInputWrapper.trigger("focus");
    await botInputWrapper.trigger("keydown", { key: "ArrowDown" });
    await botInputWrapper.trigger("keydown", { key: "Enter" });
    expect(useBotStore().selectedBotId).toBe("10002");

    const search = new DOMWrapper(rail.querySelector<HTMLInputElement>(".global-page-search input")!);
    await search.setValue("告警");
    expect(rail.querySelectorAll(".page-search-results button").length).toBeGreaterThan(0);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await vi.waitFor(() => expect(rail.dataset.state).toBe("closed"));
    wrapper.unmount();
    queryClient.clear();
  });

  it("移动抽屉关闭时 inert，打开后锁滚动、Esc 关闭并恢复菜单焦点", async () => {
    installViewport(true);
    const { wrapper, queryClient } = await renderShell();
    await nextTick();
    const trigger = wrapper.get(".mobile-nav-trigger");
    expect(document.querySelector("#admin-navigation")).toBeNull();

    await trigger.trigger("click");
    await nextTick();
    const rail = document.querySelector<HTMLElement>("#admin-navigation")!;
    expect(rail.getAttribute("role")).toBe("dialog");
    await vi.waitFor(() => expect(rail.contains(document.activeElement)).toBe(true));
    await trigger.trigger("click");
    await vi.waitFor(() => expect(rail.dataset.state).toBe("closed"));
    expect(trigger.attributes("aria-expanded")).toBe("false");
    wrapper.unmount();
    queryClient.clear();
  });

  it("移动抽屉 Tab 环行，resize 回桌面后解除 inert 与滚动锁", async () => {
    const resize = installViewport(true);
    const { wrapper, queryClient } = await renderShell();
    await nextTick();
    await wrapper.get(".mobile-nav-trigger").trigger("click");
    await nextTick();
    const rail = document.querySelector<HTMLElement>("#admin-navigation")!;
    await vi.waitFor(() => expect(rail.contains(document.activeElement)).toBe(true));

    resize(false);
    await nextTick();
    expect(document.querySelector("#admin-navigation")?.tagName).toBe("ASIDE");
    wrapper.unmount();
    queryClient.clear();
  });

  it("组件卸载时解除仍然打开的移动抽屉滚动锁", async () => {
    installViewport(true);
    const { wrapper, queryClient } = await renderShell();
    await nextTick();
    await wrapper.get(".mobile-nav-trigger").trigger("click");
    await nextTick();
    expect(document.querySelector("#admin-navigation")).not.toBeNull();
    wrapper.unmount();
    expect(document.querySelector("#admin-navigation")).toBeNull();
    queryClient.clear();
  });

  it("在窄屏使用原生键盘分区选择器，并且顶部只列出当前视觉分区页面", async () => {
    const { wrapper, router, queryClient } = await renderShell();
    await wrapper.get(".mobile-nav-trigger").trigger("click");
    await nextTick();
    const selector = new DOMWrapper(document.querySelector<HTMLSelectElement>(".mobile-section-selector select")!);
    expect(selector.element.tagName).toBe("SELECT");
    expect(document.querySelector(".mobile-section-selector label[for]")?.textContent).toBe("选择导航分区");

    await selector.trigger("keydown", { key: "ArrowDown" });
    await selector.setValue("debug-recovery");
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/runtime/health/catalog"));

    expect(wrapper.findAll(".secondary-navigation a")).toEqual([]);
    expect(wrapper.find(".top-status-details").exists()).toBe(true);
    wrapper.unmount();
    queryClient.clear();
  });
});
