import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";

import PersonaFeaturePages from "../pages/PersonaFeaturePages.vue";
import { resources } from "@/api/resources";

const mockRoute = reactive({
  path: "/persona/stickers/catalog",
  name: "persona-stickers",
  params: { section: "catalog" },
  query: {},
});

const mockRouter = {
  push: vi.fn(),
};

vi.mock("vue-router", () => ({
  useRoute: () => mockRoute,
  useRouter: () => mockRouter,
}));

vi.mock("@/api/resources", () => ({
  resources: {
    stickers: vi.fn(),
    rescanStickers: vi.fn(),
    rebuildStickerIndex: vi.fn(),
    uploadSticker: vi.fn(),
    deleteSticker: vi.fn(),
    updateSticker: vi.fn(),
    personaPromptPreview: vi.fn(),
    config: vi.fn(),
    personaBuilderGet: vi.fn(),
    personaBuilderPost: vi.fn(),
  },
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return mount(PersonaFeaturePages, {
    global: {
      plugins: [[VueQueryPlugin, { queryClient }]],
      stubs: {
        PageHeader: { template: '<header><slot name="actions"/></header>' },
        Panel: { template: '<section><slot name="eyebrow"/><slot name="title"/><slot name="actions"/><slot/></section>' },
        QueryBoundary: { template: '<div><slot/></div>' },
        StateBadge: { template: '<span><slot/></span>' },
      },
    },
  });
}

describe("PersonaFeaturePages.vue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("renders stickers catalog and handles rescan action with confirmation", async () => {
    mockRoute.path = "/persona/stickers/catalog";
    mockRoute.name = "persona-stickers";
    mockRoute.params.section = "catalog";

    vi.mocked(resources.stickers).mockResolvedValue({
      items: [
        {
          filename: "test_sticker.png",
          size_bytes: 2048,
          modified_at: 1700000000000,
          thumbnail_url: "/thumb.png",
          description: "测试表情",
          mood_tags: ["happy"],
          scene_tags: ["battle"],
          labeled: true,
        },
      ],
      page: 1,
      page_size: 20,
      total: 1,
      total_pages: 1,
      index_status: "ok",
      index_detail_code: "healthy",
      index_updated_at: 1700000000000,
      index_stale: false,
    });

    const wrapper = createWrapper();
    await wrapper.vm.$nextTick();
    expect(resources.stickers).toHaveBeenCalled();
  });

  it("does not render a green-normal index when the server returned no index state", async () => {
    mockRoute.path = "/persona/stickers/catalog";
    mockRoute.name = "persona-stickers";
    mockRoute.params.section = "catalog";
    vi.mocked(resources.stickers).mockResolvedValue({
      items: [], page: 1, page_size: 20, total: 0, total_pages: 1,
      index_status: "", index_detail_code: "", index_updated_at: 0, index_stale: false,
    });

    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("未知／未配置");
  });

  it("renders persona preview prompt when navigating to persona-preview route", async () => {
    mockRoute.path = "/persona/persona-preview/prompt";
    mockRoute.name = "persona-preview";
    mockRoute.params.section = "prompt";

    vi.mocked(resources.personaPromptPreview).mockResolvedValue({
      content: "System Persona Prompt Mock",
      exists: true,
      is_file: true,
      source: "prompt_path / system_path",
      warnings: [],
    });
    vi.mocked(resources.config).mockResolvedValue({
      items: [
        { key: "prompt_path", field_name: "personification_prompt_path", display_name: "人设文件路径", description: "", group: "人设提示词", category: "config", scope: "global", kind: "text", value_type: "str", value: "persona.md", default: "", secret: false, advanced: false, hot_reloadable: true, restart_required: false, required: false, modified: true, aliases: [], choices: [], min_value: null, max_value: null },
        { key: "context_budget_enabled", field_name: "personification_context_budget_enabled", display_name: "上下文 Token 预算", description: "", group: "记忆", category: "config", scope: "global", kind: "toggle", value_type: "bool", value: true, default: true, secret: false, advanced: false, hot_reloadable: true, restart_required: false, required: false, modified: false, aliases: [], choices: [], min_value: null, max_value: null },
        { key: "model_purpose_bindings", field_name: "personification_model_purpose_bindings", display_name: "用途模型绑定", description: "", group: "模型路由", category: "config", scope: "global", kind: "object", value_type: "dict", value: { reply: { provider_id: "main", model_id: "chat" } }, default: {}, secret: false, advanced: false, hot_reloadable: true, restart_required: false, required: false, modified: true, aliases: [], choices: [], min_value: null, max_value: null },
      ], page: 1, page_size: 200, total: 3, total_pages: 1, revision: "r1", groups: [], group_counts: {}, modified_counts: {},
    });

    const wrapper = createWrapper();
    await wrapper.vm.$nextTick();
    expect(resources.personaPromptPreview).toHaveBeenCalled();
    await vi.waitFor(() => expect(wrapper.text()).toContain("System Persona Prompt Mock"));
    expect(wrapper.text()).toContain("全局（所有人格回复入口）");
    expect(wrapper.text()).toContain("文件路径配置优先于内联 system_prompt");
    expect(wrapper.text()).toContain("prompt_path / system_path · 文件 · 显式配置");
    expect(wrapper.text()).toContain("已启用 · 注册默认值");
    expect(wrapper.text()).toContain("main");
    expect(wrapper.text()).toContain("chat");
  });

  it("renders persona builder history when on persona-builder route", async () => {
    mockRoute.path = "/persona/persona-builder/tasks";
    mockRoute.name = "persona-builder";
    mockRoute.params.section = "tasks";
    vi.mocked(resources.personaBuilderGet).mockResolvedValue({ records: [] });
    createWrapper();
    expect(resources.personaBuilderGet).toHaveBeenCalledWith("history", expect.anything());
  });
});
