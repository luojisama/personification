import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { Page, RouteCapabilityItem } from "@/api/types";
import RouteCapabilitiesPage from "./RouteCapabilitiesPage.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    routes: vi.fn(),
    queueRouteProbe: vi.fn(),
  },
}));

const mockRouteItem: RouteCapabilityItem = {
  route_fingerprint: "rf_test_1234567890",
  provider: "openai",
  api_type: "chat_completions",
  model: "gpt-4o",
  media_protocol: "native_base64",
  probe_status: "idle",
  capabilities: {
    image_input: { state: "supported", source: "runtime_success", checked_at: 1700000000000, expires_at: null, detail_code: "ok" },
    audio_input: { state: "unsupported", source: "provider_catalog", checked_at: 1700000000000, expires_at: null, detail_code: "unsupported_by_schema" },
    video_input: { state: "unknown", source: "heuristic", checked_at: null, expires_at: null, detail_code: "never_probed" },
    reasoning: { state: "supported", source: "model_catalog", checked_at: 1700000000000, expires_at: null, detail_code: "ok" },
    function_call: { state: "supported", source: "runtime_success", checked_at: 1700000000000, expires_at: null, detail_code: "ok" },
    native_web_search: { state: "unsupported", source: "heuristic", checked_at: null, expires_at: null, detail_code: "no_search_tool" },
    external_network_access: { state: "unknown", source: "heuristic", checked_at: null, expires_at: null, detail_code: "policy_unspecified" },
  },
};

const mockPageData: Page<RouteCapabilityItem> = {
  items: [mockRouteItem],
  page: 1,
  page_size: 20,
  total: 1,
  total_pages: 1,
};

describe("RouteCapabilitiesPage", () => {
  beforeEach(() => {
    vi.mocked(resources.routes).mockResolvedValue(mockPageData);
    vi.mocked(resources.queueRouteProbe).mockResolvedValue({
      ok: true,
      code: "probe_queued",
      phase: "dispatch",
      title: "探针已入队",
      message: "测试探针已排队",
      retryable: false,
      partial: false,
      outcome_unknown: false,
      warnings: [],
      steps: [],
    });
  });

  async function renderPage() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/routes/:section", component: RouteCapabilitiesPage }],
    });
    await router.push("/runtime/routes/capabilities");
    await router.isReady();

    const wrapper = mount(RouteCapabilitiesPage, {
      global: { plugins: [[VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, queryClient };
  }

  it("正确渲染 Provider、模型卡片以及 supported/unsupported/unknown 三态能力标记", async () => {
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("gpt-4o"));
    expect(wrapper.text()).toContain("openai / chat_completions");
    expect(wrapper.text()).toContain("支持 3");
    expect(wrapper.text()).toContain("不支持 2");
    expect(wrapper.text()).toContain("未知 2");
    wrapper.unmount();
    queryClient.clear();
  });
});
