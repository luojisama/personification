import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import CapabilityBusinessPages from "./CapabilityBusinessPages.vue";
import { resources } from "@/api/resources";
import SourceCoverage from "@vue-app/components/SourceCoverage.vue";

const mockRoute = {
  path: "/capability/skills/installed",
  params: { section: "installed" },
  meta: { mode: "skills" },
};

vi.mock("vue-router", () => ({
  useRoute: () => mockRoute,
}));

vi.mock("@/api/resources", () => ({
  resources: {
    catalog: vi.fn(),
    skillAction: vi.fn(),
    mcpGet: vi.fn(),
    mcpPost: vi.fn(),
    toolCreatorGet: vi.fn(),
    toolCreatorPost: vi.fn(),
    pluginKnowledgeSearch: vi.fn(),
    pluginKnowledgeDetail: vi.fn(),
    pluginKnowledgeStatus: vi.fn(),
    pluginKnowledgeSection: vi.fn(),
    startPluginKnowledgeBuild: vi.fn(),
    cancelPluginKnowledgeBuild: vi.fn(),
    pluginUpdateStatus: vi.fn(),
    pluginUpdateBenchmark: vi.fn(),
    pluginUpdateCheck: vi.fn(),
    pluginUpdateApply: vi.fn(),
    pluginUpdateHistory: vi.fn(),
  },
}));

describe("CapabilityBusinessPages.vue", () => {
  it("distinguishes missing coverage counters from a real zero", () => {
    const missing = mount(SourceCoverage, { props: { coverage: {} } });
    const zero = mount(SourceCoverage, {
      props: { coverage: { source_file_count: 0, source_chunk_count: 0, source_chars: 0 } },
    });

    expect(missing.text()).toContain("文件—");
    expect(zero.text()).toContain("文件0");
  });
  let queryClient: QueryClient;
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    vi.clearAllMocks();
    pinia = createPinia();
    setActivePinia(pinia);
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    window.confirm = vi.fn().mockReturnValue(true);
    vi.mocked(resources.catalog).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0, total_pages: 0 });
    vi.mocked(resources.pluginKnowledgeStatus).mockResolvedValue({
      available: true,
      automatic_build_enabled: false,
      state: "stale_pending",
      counts: { loaded: 5, indexed: 2, missing: 3, pending: 3, failed: 0, degraded: 0, success: 2 },
      operation: null,
      diagnostic_code: "plugin_knowledge_automatic_build_disabled",
    });
    vi.mocked(resources.pluginKnowledgeSection).mockResolvedValue({ items: [], total: 0 });
  });

  function createWrapper(props: { mode?: "skills" | "mcp" | "tool-creator" | "plugin-knowledge" | "plugins" } = {}) {
    return mount(CapabilityBusinessPages, {
      props,
      global: {
        plugins: [
          pinia,
          [VueQueryPlugin, { queryClient }],
        ],
      },
    });
  }

  it("renders SkillsPage and toggles a skill upon confirmation", async () => {
    mockRoute.path = "/capability/skills/installed";
    mockRoute.params.section = "installed";
    mockRoute.meta.mode = "skills";

    vi.mocked(resources.catalog).mockResolvedValue({
      items: [
        { name: "weather_tool", description: "查询天气", source_kind: "builtin", user_disabled: false },
      ],
      page: 1,
      page_size: 20,
      total: 1,
      total_pages: 1,
    });

    vi.mocked(resources.skillAction).mockResolvedValueOnce({ ok: true, code: "skill_toggle_ok" });

    const wrapper = createWrapper({ mode: "skills" });
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("Skill 管理");
    });

    expect(resources.catalog).toHaveBeenCalledWith("skills", 1, 20, "", expect.anything());
  });

  it("renders McpManagementPage and handles reload confirmation", async () => {
    mockRoute.path = "/capability/mcp/registry";
    mockRoute.params.section = "registry";
    mockRoute.meta.mode = "mcp";

    vi.mocked(resources.mcpGet).mockResolvedValueOnce({
      servers: [
        { name: "filesystem", source_id: "official", description: "文件访问服务", status: "ready" },
      ],
    });
    vi.mocked(resources.mcpPost).mockResolvedValueOnce({ ok: true, code: "mcp_reloaded" });

    const wrapper = createWrapper({ mode: "mcp" });
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("MCP 管理");
    });

    const reloadBtn = wrapper.findAll("button").find((btn) => btn.text().includes("重载 MCP"));
    expect(reloadBtn).toBeDefined();
    await reloadBtn?.trigger("click");
    expect(window.confirm).toHaveBeenCalledWith("确认重载 MCP process 与工具目录？");
    expect(resources.mcpPost).toHaveBeenCalledWith("reload");
  });

  it("renders ToolCreatorPage and validates form submission guard", async () => {
    mockRoute.path = "/capability/tool-creator/tasks";
    mockRoute.params.section = "tasks";
    mockRoute.meta.mode = "tool-creator";

    vi.mocked(resources.toolCreatorGet).mockResolvedValueOnce({
      tasks: [
        { task_id: "task_01", suggested_name: "calculator", request_text: "计算器", status: "pending", version: 1 },
      ],
    });
    vi.mocked(resources.toolCreatorPost).mockResolvedValueOnce({ ok: true, code: "task_created" });

    const wrapper = createWrapper({ mode: "tool-creator" });
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("创建工具");
    });

    const submitBtn = wrapper.findAll("button").find((btn) => btn.text() === "创建任务");
    expect(submitBtn?.attributes("disabled")).toBeDefined();
  });

  it("renders PluginKnowledgePage and supports knowledge browsing", async () => {
    mockRoute.path = "/capability/plugin-knowledge/catalog";
    mockRoute.params.section = "catalog";
    mockRoute.meta.mode = "plugin-knowledge";

    vi.mocked(resources.catalog).mockResolvedValue({
      items: [
        { plugin_name: "core_ops", display_name: "核心运维", summary: "提供日志与诊断能力", category: "ops", source_coverage: { source_file_count: 4, source_chunk_count: 9, source_chars: 1200, analysis_strategy: "module_bundles", full_input: true } },
      ],
      page: 1,
      page_size: 20,
      total: 1,
      total_pages: 1,
    });

    const wrapper = createWrapper({ mode: "plugin-knowledge" });
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("插件知识库");
      expect(wrapper.text()).toContain("源码字符");
      expect(wrapper.text()).not.toContain("[object Object]");
    });
    wrapper.unmount();
  });

  it("uses object items when search keeps the legacy string results field", async () => {
    mockRoute.path = "/capability/plugin-knowledge/search";
    mockRoute.params.section = "search";
    mockRoute.meta.mode = "plugin-knowledge";
    vi.mocked(resources.pluginKnowledgeSearch).mockResolvedValue({
      results: ["core_ops"],
      items: [{ plugin_name: "core_ops", display_name: "核心运维", summary: "真实对象命中", category: "ops", source_coverage: { source_file_count: 1 } }],
    });
    const wrapper = createWrapper({ mode: "plugin-knowledge" });
    const input = wrapper.find("input[type='search']");
    await input.setValue("core");
    await vi.waitFor(() => expect(wrapper.text()).toContain("真实对象命中"));
    expect(wrapper.text()).not.toContain("[object Object]");
    wrapper.unmount();
  });

  it("renders PluginManagementPage and enforces UPDATE confirmation text for apply", async () => {
    mockRoute.path = "/capability/plugins/update";
    mockRoute.params.section = "update";
    mockRoute.meta.mode = "plugins";

    vi.mocked(resources.pluginUpdateStatus).mockResolvedValueOnce({
      available: true,
      update_supported: true,
      source_type: "git",
      dirty: false,
      dirty_count: 0,
      update_available: true,
      ahead: 0,
      behind: 2,
      source: { upstream: "origin/main" },
      local: { branch: "main", short_hash: "a1b2c3d" },
      remote: { short_hash: "e5f6a7b" },
      pending_history: [],
    });

    const wrapper = createWrapper({ mode: "plugins" });
    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("插件管理");
      expect(wrapper.text()).toContain("执行更新");
    });
  });
});
