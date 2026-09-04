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
import { resources } from "@/api/resources";
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
          ui_schema: { control_kind: "text", placeholder: "输入昵称" },
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
          ui_schema: {
            control_kind: "string_list",
            item_schema: { type: "string", label: "插件", placeholder: "输入插件名" },
          },
        },
        {
          key: "unknown_metadata",
          field_name: "unknown_metadata",
          display_name: "未知元数据",
          description: "由扩展写入的未知结构。",
          group: "基础配置",
          category: "general",
          scope: "global",
          kind: "dict",
          value_type: "dict",
          value: { enabled: true },
          default: {},
          secret: false,
          advanced: true,
          hot_reloadable: true,
          restart_required: false,
          required: false,
          modified: false,
          aliases: [],
          choices: [],
          min_value: null,
          max_value: null,
          ui_schema: { control_kind: "json_advanced", placeholder: "输入 JSON 对象" },
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

  it("仅对未知结构在 JSON 抽屉中校验，并关联可访问错误", async () => {
    const wrapper = mountComponent();
    await flushPromises();
    expect(wrapper.find("#config-allowed_plugins-item-0").exists()).toBe(true);
    expect(wrapper.text()).toContain("添加插件");

    await wrapper.get("#config-bot_name").setValue("新的昵称");
    expect(wrapper.get(".config-save-actions button").attributes("disabled")).toBeUndefined();

    await wrapper.get("#config-unknown_metadata").trigger("click");
    const textarea = wrapper.get("#config-unknown_metadata-json");
    await textarea.setValue("not valid json");
    const verifyButton = wrapper.findAll("button").find((button) => button.text() === "校验并应用草稿");
    expect(verifyButton).toBeTruthy();
    await verifyButton?.trigger("click");
    expect(wrapper.get("#config-unknown_metadata-json-error").text()).toContain("JSON");
    expect(textarea.attributes("aria-invalid")).toBe("true");
    expect(wrapper.get(".config-save-actions button").attributes("disabled")).toBeDefined();
  });

  it("以共享确认对话框提交原子配置保存，而不调用浏览器确认框", async () => {
    const wrapper = mountComponent();
    await flushPromises();

    await wrapper.get("#config-bot_name").setValue("新的昵称");
    await wrapper.get(".config-save-actions button").trigger("click");

    expect(wrapper.get("[role=dialog]").text()).toContain("原子保存配置草稿");
    const confirmButton = wrapper.findAll("button").find((button) => button.text() === "确认保存");
    expect(confirmButton).toBeTruthy();
    await confirmButton?.trigger("click");
    await flushPromises();

    expect(resources.patchConfig).toHaveBeenCalledWith("rev-001", { bot_name: "新的昵称" });
    expect(wrapper.find("[role=dialog]").exists()).toBe(false);
  });

  it("分类筛选后保留上一页结果并将结果容器回到顶部", async () => {
    const wrapper = mountComponent();
    await flushPromises();

    const resultContainer = wrapper.get(".config-results").element as HTMLElement;
    resultContainer.scrollTop = 180;
    type ConfigResult = Awaited<ReturnType<typeof resources.config>>;
    let resolveNext: ((value: ConfigResult) => void) | undefined;
    vi.mocked(resources.config).mockImplementationOnce(() => new Promise<ConfigResult>((resolve) => {
      resolveNext = resolve;
    }));

    const categoryButton = wrapper.findAll(".config-category-rail button").find((button) => button.text().includes("基础配置"));
    expect(categoryButton).toBeTruthy();
    await categoryButton?.trigger("click");
    await flushPromises();

    expect(resultContainer.scrollTop).toBe(0);
    expect(wrapper.text()).toContain("机器人昵称");

    resolveNext?.({
      revision: "rev-001",
      page: 1,
      page_size: 20,
      total: 0,
      total_pages: 1,
      groups: ["基础配置"],
      group_counts: { "基础配置": 0 },
      modified_counts: {},
      items: [],
    });
    await flushPromises();
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
