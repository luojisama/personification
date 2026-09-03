import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";

import ProactiveDiagnosticsPage from "./ProactiveDiagnosticsPage.vue";
import { resources } from "@/api/resources";

const mockPush = vi.fn();
const mockRoute = reactive({
  params: { section: "overview" },
  query: {} as Record<string, string>,
  path: "/runtime/proactive/overview",
});

vi.mock("vue-router", () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("@/api/resources", () => ({
  resources: {
    proactiveStats: vi.fn(),
    proactiveRecent: vi.fn(),
    proactiveNextEligible: vi.fn(),
  },
}));

describe("ProactiveDiagnosticsPage.vue", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    mockRoute.params.section = "overview";
    mockRoute.query = {};
    mockRoute.path = "/runtime/proactive/overview";
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, gcTime: 0 },
      },
    });
  });

  it("renders overview section with metrics and outcome distribution", async () => {
    vi.mocked(resources.proactiveStats).mockResolvedValueOnce({
      scope: "",
      since_hours: 72,
      counts: { sent: 12, skip_cooldown: 4 },
      sent: 12,
      skip: 4,
      total: 16,
    });

    const wrapper = mount(ProactiveDiagnosticsPage, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient }]],
        stubs: {
          PageHeader: { template: "<header><slot name='actions'/></header>" },
          Panel: { template: "<section><slot name='eyebrow'/><slot name='title'/><slot/></section>" },
          QueryBoundary: { template: "<div><slot/></div>" },
          StateBadge: { template: "<span><slot/></span>" },
        },
      },
    });

    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("触发总数");
      expect(wrapper.text()).toContain("16");
      expect(wrapper.text()).toContain("已发送");
      expect(wrapper.text()).toContain("12");
      expect(wrapper.text()).toContain("冷却中");
    });
  });

  it("handles scope and outcome filter updates via router push", async () => {
    mockRoute.params.section = "recent";
    mockRoute.path = "/runtime/proactive/recent";
    mockRoute.query = { cursor: "123" };

    vi.mocked(resources.proactiveRecent).mockResolvedValueOnce({
      items: [
        {
          id: 1,
          ts: 1710000000000,
          scope: "private",
          target: "10001",
          outcome: "sent",
          detail: { action: "greeting" },
          next_eligible_at: 1710003600000,
        },
      ],
      next_cursor: 0,
      has_more: false,
      limit: 50,
      filters: {},
    });

    const wrapper = mount(ProactiveDiagnosticsPage, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient }]],
        stubs: {
          PageHeader: { template: "<header><slot name='actions'/></header>" },
          Panel: { template: "<section><slot/></section>" },
          QueryBoundary: { template: "<div><slot/></div>" },
          StateBadge: { template: "<span><slot/></span>" },
        },
      },
    });

    const privateScopeButton = wrapper.findAll(".segmented-control button")[1];
    expect(privateScopeButton).toBeDefined();
    await privateScopeButton!.trigger("click");
    expect(mockPush).toHaveBeenCalledWith({ query: { scope: "private" } });
  });
});
