import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import { reactive } from "vue";

import QzoneCapabilitiesPage from "./QzoneCapabilitiesPage.vue";
import { useBotStore } from "@vue-app/stores/bot";
import { resources } from "@/api/resources";

const currentRoute = reactive<{ params: Record<string, string> }>({
  params: { section: "capabilities" },
});

vi.mock("vue-router", () => ({
  useRoute: () => currentRoute,
}));

vi.mock("@/api/resources", () => ({
  resources: {
    qzoneCapabilities: vi.fn(),
    qzoneGet: vi.fn(),
    qzonePost: vi.fn(),
  },
}));

describe("QzoneCapabilitiesPage.vue", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    setActivePinia(createPinia());
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    vi.clearAllMocks();
    currentRoute.params = { section: "capabilities" };
  });

  function createWrapper() {
    return mount(QzoneCapabilitiesPage, {
      global: {
        plugins: [createPinia(), [VueQueryPlugin, { queryClient }]],
        stubs: {
          DiagnosticPanel: { template: '<div class="diagnostic-panel-stub" />' },
          EmptyState: { template: '<div class="empty-state-stub"><slot /></div>' },
          PageHeader: {
            props: ["index", "title", "description"],
            template: '<header class="page-header-stub"><h1>{{ title }}</h1><p>{{ description }}</p></header>',
          },
          Panel: {
            props: ["eyebrow", "title"],
            template: '<section class="panel-stub"><h2>{{ title }}</h2><slot /></section>',
          },
          QueryBoundary: {
            props: ["pending", "error"],
            template: '<div class="query-boundary-stub"><slot /></div>',
          },
          StateBadge: {
            props: ["tone"],
            template: '<span class="state-badge-stub"><slot /></span>',
          },
        },
      },
    });
  }

  it("renders capability matrix with structured states and unknown distinct from available", async () => {
    const botStore = useBotStore();
    botStore.setBotId("10001");

    vi.mocked(resources.qzoneCapabilities).mockResolvedValueOnce({
      items: [
        {
          action: "publish",
          state: "available",
          interface: "qzone.publish",
          http_status: 200,
          business_code: 0,
          auth_state: "authenticated",
          detail_code: "ok",
        },
        {
          action: "top_level_comment",
          state: "unknown",
          interface: "qzone.comment",
          http_status: null,
          business_code: null,
          auth_state: "unknown",
          detail_code: "unverified",
        },
      ],
    });

    const wrapper = createWrapper();
    await flushPromises();

    expect(resources.qzoneCapabilities).toHaveBeenCalled();
    const text = wrapper.text();
    expect(text).toContain("发布");
    expect(text).toContain("顶级评论");
    expect(text).toContain("available");
    expect(text).toContain("unknown");
  });

  it("renders status in auth section and displays read-only status and quota correctly", async () => {
    currentRoute.params = { section: "auth" };
    const botStore = useBotStore();
    botStore.setBotId("10002");

    vi.mocked(resources.qzoneGet).mockImplementation(async (path: string) => {
      if (path === "status") {
        return {
          enabled: true,
          cookie_configured: true,
          read_only: true,
          auth: { state: "valid" },
          quota: { used: 12, limit: 100 },
          reconciliation: { state: "clear", blocking: false, operations: [] },
        };
      }
      return {};
    });

    const wrapper = createWrapper();
    await flushPromises();

    expect(resources.qzoneGet).toHaveBeenCalledWith("status", {}, expect.anything());
    const text = wrapper.text();
    expect(text).toContain("登录与运行态");
    expect(text).toContain("额度与结果核对");
    expect(text).toContain("已配置（不回传原值）");
  });

  it("keeps unsupported write operations disabled in operations section", async () => {
    currentRoute.params = { section: "operations" };
    const wrapper = createWrapper();
    await flushPromises();

    const buttons = wrapper.findAll("button[disabled]");
    const disabledLabels = buttons.map((b) => b.text());
    expect(disabledLabels).toContain("点赞");
    expect(disabledLabels).toContain("转发");
  });
});
