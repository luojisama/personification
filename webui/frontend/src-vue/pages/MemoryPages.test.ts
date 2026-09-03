import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
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
});
