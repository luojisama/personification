import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";

import MemoryPages from "./MemoryPages.vue";
import { resources } from "@/api/resources";

vi.mock("@/api/resources", () => ({
  resources: {
    catalog: vi.fn(),
    memoryPage: vi.fn(),
    memoryZonePage: vi.fn(),
    memoryBusiness: vi.fn(),
    memorySearch: vi.fn(),
    rebuildMemoryIndex: vi.fn(),
  },
}));

describe("MemoryPages.vue", () => {
  let router: ReturnType<typeof createRouter>;
  let queryClient: QueryClient;

  beforeEach(async () => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });

    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/persona/memories/:section", name: "persona-memories", component: MemoryPages },
        { path: "/persona/memory-palace/:section", name: "persona-memory-palace", component: MemoryPages },
      ],
    });
  });

  it("renders recent memories table with structured columns", async () => {
    vi.mocked(resources.memoryPage).mockResolvedValue({
      items: [
        { id: "mem_1", summary: "用户喜欢喝乌龙茶", scope: "group", source: "dialogue", status: "active", expires_at: null },
      ],
      page: 1,
      page_size: 20,
      total: 1,
      total_pages: 1,
    });

    await router.push("/persona/memories/recent");
    await router.isReady();

    const wrapper = mount(MemoryPages, {
      global: {
        plugins: [router, [VueQueryPlugin, { queryClient }]],
      },
    });

    expect(wrapper.text()).toContain("Agent 记忆与记忆宫殿");
    await flushPromises();
    expect(resources.memoryPage).toHaveBeenCalledWith(1, 20, expect.objectContaining({ search: "" }), expect.anything());
    expect(wrapper.text()).toContain("群聊");
    expect(wrapper.text()).toContain("聊天对话");
    expect(wrapper.text()).toContain("有效");
    expect(wrapper.find(".state-badge").attributes("title")).toBe("active");
  });

  it("uses Chinese labels for known memory enums and a diagnostic raw value for unknown ones", async () => {
    vi.mocked(resources.memoryPage).mockResolvedValue({
      items: [{ id: "mem_unknown", summary: "合成记录", scope: "unrecognized_scope", source_kind: "unrecognized_source", status: "unrecognized_status", expires_at: null }],
      page: 1, page_size: 20, total: 1, total_pages: 1,
    });

    await router.push("/persona/memories/recent");
    await router.isReady();
    const wrapper = mount(MemoryPages, { global: { plugins: [router, [VueQueryPlugin, { queryClient }]] } });
    await flushPromises();

    const cells = wrapper.findAll("tbody td");
    const [, scope, source, status] = cells;
    expect(scope).toBeDefined();
    expect(source).toBeDefined();
    expect(status).toBeDefined();
    if (!scope || !source || !status) throw new Error("memory row did not render expected cells");
    expect(scope.text()).toBe("未知");
    expect(scope.attributes("title")).toBe("unrecognized_scope");
    expect(source.text()).toBe("未知");
    expect(source.attributes("title")).toBe("unrecognized_source");
    expect(status.text()).toContain("未知");
    expect(status.find(".state-badge").attributes("title")).toBe("unrecognized_status");
  });

  it("requires explicit window.confirm before triggering rebuild", async () => {
    vi.mocked(resources.memoryBusiness).mockResolvedValue({
      status: "ready",
      document_count: 42,
      updated_at: 1710000000000,
      diagnostic_code: "vector_index_ready",
    });
    vi.mocked(resources.memoryZonePage).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0, total_pages: 1 });
    vi.mocked(resources.rebuildMemoryIndex).mockResolvedValue({ diagnostic_code: "rebuild_queued" });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await router.push("/persona/memories/vector-index");
    await router.isReady();

    const wrapper = mount(MemoryPages, {
      global: {
        plugins: [router, [VueQueryPlugin, { queryClient }]],
      },
    });

    const btn = wrapper.find("button.button-danger");
    await btn.trigger("click");
    expect(confirmSpy).toHaveBeenCalled();
    expect(resources.rebuildMemoryIndex).not.toHaveBeenCalled();
  });

  it("renders API embedding status as unverified instead of healthy connectivity", async () => {
    vi.mocked(resources.memoryBusiness).mockResolvedValue({
      status: "ready", document_count: 42, diagnostic_code: "vector_index_ready",
      embedding: {
        enabled: true, state: "ready", connectivity_state: "unknown",
        indexed: 18, total: 20, pending: 2, rebuilding: true,
        provider: "OpenAIEmbeddingProvider", model: "text-embedding-3-small",
        dimension: 1536, diagnostic_code: "embedding_status_observed",
      },
    });
    await router.push("/persona/memories/vector-index");
    await router.isReady();
    const wrapper = mount(MemoryPages, { global: { plugins: [router, [VueQueryPlugin, { queryClient }]] } });
    await flushPromises();
    expect(wrapper.text()).toContain("Embedding API 索引状态");
    expect(wrapper.text()).toContain("未核验");
    expect(wrapper.text()).toContain("18 / 20");
    expect(wrapper.text()).toContain("text-embedding-3-small");
  });

  it("点击 v2 宫殿分区后，缺少后端 entries 时明确展示空态而不伪造条目", async () => {
    vi.mocked(resources.memoryBusiness).mockResolvedValue({
      schema_version: 2,
      zone_details: [
        {
          zone_id: "person",
          name: "人物记忆",
          purpose: "保留用户长期信息。",
          status: "not_configured",
          item_count: 3,
          last_updated_at: 1710000000000,
        },
      ],
    });

    await router.push("/persona/memory-palace/palace-zones");
    await router.isReady();
    const wrapper = mount(MemoryPages, {
      global: {
        plugins: [router, [VueQueryPlugin, { queryClient }]],
      },
    });
    await flushPromises();

    const zone = wrapper.get("button.palace-zone-button");
    expect(zone.attributes("aria-label")).toContain("人物记忆");
    await zone.trigger("click");
    expect(wrapper.text()).toContain("条目数");
    await flushPromises();
    expect(wrapper.text()).toContain("当前分区暂无可展示条目");
    expect(wrapper.text()).not.toContain("memory_1");
  });
});
