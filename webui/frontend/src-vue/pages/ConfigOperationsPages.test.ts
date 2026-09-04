import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createPinia } from "pinia";
import { ref, shallowRef } from "vue";
import {
  RUNTIME_EVENTS_KEY,
  type RuntimeEventsManager,
} from "@vue-app/realtime/runtimeEvents";
import ConfigOperationsPages from "./ConfigOperationsPages.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    configMetadata: vi.fn().mockResolvedValue({
      revision: "rev-001",
      groups: ["基础配置", "模型设置"],
      group_counts: { "基础配置": 10, "模型设置": 5 },
      modified_counts: { "基础配置": 1, "模型设置": 0 },
      total: 15,
      diagnostic_code: "ok",
    }),
    config: vi.fn().mockResolvedValue({
      revision: "rev-001",
      page: 1,
      page_size: 20,
      total: 2,
      total_pages: 1,
      groups: ["基础配置"],
      group_counts: { "基础配置": 1 },
      modified_counts: {},
      items: [
        {
          key: "bot_name",
          field_name: "bot_name",
          display_name: "机器人昵称",
          description: "机器人全局对外展示名",
          group: "基础配置",
          category: "general",
          scope: "global",
          kind: "string",
          value_type: "str",
          value: "PersonificationBot",
          default: "Bot",
          secret: false,
          advanced: false,
          hot_reloadable: true,
          restart_required: false,
          required: true,
          modified: false,
          aliases: ["name"],
          choices: [],
          min_value: null,
          max_value: null,
        },
        {
          key: "allowed_plugins",
          field_name: "allowed_plugins",
          display_name: "插件白名单",
          description: "允许加载的插件列表",
          group: "基础配置",
          category: "general",
          scope: "global",
          kind: "list",
          value_type: "list",
          value: ["echo", "ping"],
          default: [],
          secret: false,
          advanced: false,
          hot_reloadable: true,
          restart_required: false,
          required: false,
          modified: false,
          aliases: [],
          choices: [],
          min_value: null,
          max_value: null,
        },
      ],
    }),
    patchConfig: vi.fn().mockResolvedValue({
      revision: "rev-002",
      updated_keys: ["bot_name"],
      hot_reloaded_keys: ["bot_name"],
      restart_required_keys: [],
      warnings: [],
    }),
    runtimeSettings: vi.fn().mockResolvedValue({
      revision: "rev-001",
      participation_v2_mode: "shadow",
    }),
    logs: vi.fn().mockResolvedValue({
      items: [],
      next_cursor: 0,
      has_more: false,
      limit: 100,
      filters: {},
    }),
    clearLogs: vi.fn().mockResolvedValue({
      ok: true,
      code: "logs_cleared",
    }),
  },
}));

function createMockRuntimeEvents(): RuntimeEventsManager {
  return {
    events: shallowRef([]),
    state: ref("open"),
    resyncCount: ref(0),
    client: shallowRef(null),
    start: vi.fn(),
    stop: vi.fn(),
  };
}

describe("ConfigOperationsPages.vue", () => {
  let router: ReturnType<typeof createRouter>;
  let queryClient: QueryClient;
  let pinia: ReturnType<typeof createPinia>;
  let runtimeEvents: RuntimeEventsManager;

  beforeEach(async () => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    pinia = createPinia();
    runtimeEvents = createMockRuntimeEvents();
    router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/operations/config/all", component: ConfigOperationsPages }],
    });
    await router.push("/operations/config/all");
  });

  function mountComponent(mode: "config" | "settings" | "logs" = "config") {
    return mount(ConfigOperationsPages, {
      props: { mode },
      global: {
        plugins: [router, [VueQueryPlugin, { queryClient }], pinia],
        provide: {
          [RUNTIME_EVENTS_KEY as symbol]: runtimeEvents,
        },
      },
    });
  }

  it("renders config center and displays metadata", async () => {
    const wrapper = mountComponent();
    await flushPromises();
    expect(wrapper.text()).toContain("配置中心");
  });

  it("阻止保存无效的列表 JSON，并关联可访问错误", async () => {
    const wrapper = mountComponent();
    await flushPromises();
    const textarea = wrapper.get("#config-allowed_plugins");
    await textarea.setValue("not valid json");
    expect(wrapper.get("#config-allowed_plugins-error").text()).toContain("JSON");
    expect(textarea.attributes("aria-invalid")).toBe("true");
    expect(wrapper.get(".config-save-actions button").attributes("disabled")).toBeDefined();
  });

  it("设置页使用 Vue 三主题与简约明暗模式", async () => {
    const wrapper = mountComponent("settings");
    await flushPromises();
    expect(wrapper.text()).toContain("简约");
    expect(wrapper.text()).toContain("夏莱");
    expect(wrapper.text()).toContain("PRTS");
    expect(wrapper.text()).toContain("跟随系统");
  });
});
