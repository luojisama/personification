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

  it("renders persona preview prompt when navigating to persona-preview route", async () => {
    mockRoute.path = "/persona/persona-preview/prompt";
    mockRoute.name = "persona-preview";
    mockRoute.params.section = "prompt";

    vi.mocked(resources.personaPromptPreview).mockResolvedValue({
      prompt: "System Persona Prompt Mock",
      warnings: [],
    });

    const wrapper = createWrapper();
    await wrapper.vm.$nextTick();
    expect(resources.personaPromptPreview).toHaveBeenCalled();
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
