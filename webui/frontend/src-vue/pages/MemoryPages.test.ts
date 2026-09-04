import { describe, it, expect, vi, beforeEach } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";

import MemoryPages from "./MemoryPages.vue";
import { resources } from "@/api/resources";

vi.mock("@/api/resources", () => ({
  resources: {
    catalog: vi.fn(),
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
    vi.mocked(resources.catalog).mockResolvedValue({
      items: [
        { id: "mem_1", summary: "用户喜欢喝乌龙茶", scope: "group_123", source: "dialogue", status: "active", expires_at: null },
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
    expect(resources.catalog).toHaveBeenCalledWith("memories", 1, 20, "", expect.anything());
  });

  it("requires explicit window.confirm before triggering rebuild", async () => {
    vi.mocked(resources.memoryBusiness).mockResolvedValue({
      status: "ready",
      document_count: 42,
      updated_at: 1710000000000,
      diagnostic_code: "vector_index_ready",
    });
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
    expect(wrapper.text()).toContain("后端暂未提供该分区的条目明细");
    expect(wrapper.text()).not.toContain("memory_1");
  });
});
