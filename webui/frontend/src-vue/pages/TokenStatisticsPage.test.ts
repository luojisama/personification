import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { TokenSummary } from "@/api/types";
import TokenStatisticsPage from "./TokenStatisticsPage.vue";

vi.mock("@/api/resources", () => ({ resources: { metrics: vi.fn() } }));

const mockTokenSummary: TokenSummary = {
  window: "day",
  generated_at: 1700000000000,
  total: {
    prompt_tokens: 12000,
    completion_tokens: 4000,
    total_tokens: 16000,
    call_count: 20,
  },
  series: [
    { bucket: "10:00", prompt_tokens: 6000, completion_tokens: 2000, total_tokens: 8000, call_count: 10 },
    { bucket: "11:00", prompt_tokens: 6000, completion_tokens: 2000, total_tokens: 8000, call_count: 10 },
  ],
  by_model: [
    { model: "gpt-4o", prompt_tokens: 12000, completion_tokens: 4000, total_tokens: 16000, call_count: 20 },
  ],
  by_group: [],
  by_purpose: [],
  provider_usage: [
    {
      provider: "openai",
      label: "OpenAI 官方",
      prompt_tokens: 12000,
      completion_tokens: 4000,
      total_tokens: 16000,
      call_count: 20,
      monthly_limit: 100000,
      usage_ratio: 0.16,
      unlimited: false,
    },
  ],
  billing: {
    cost_configured: true,
    currency: "CNY",
    request_cost: 0.0352,
    note: "按标准倍率计算",
  },
};

describe("TokenStatisticsPage", () => {
  beforeEach(() => {
    vi.mocked(resources.metrics).mockResolvedValue(mockTokenSummary);
  });

  async function renderPage(initialPath = "/runtime/tokens/24h") {
    const pinia = createPinia();
    setActivePinia(pinia);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/tokens/:window", component: TokenStatisticsPage }],
    });
    await router.push(initialPath);
    await router.isReady();

    const wrapper = mount(TokenStatisticsPage, {
      global: { plugins: [pinia, [VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, router, queryClient };
  }

  it("由路由参数权威驱动时间窗口并渲染 Token 统计与费用", async () => {
    const { wrapper, router, queryClient } = await renderPage("/runtime/tokens/7d");
    await vi.waitFor(() => expect(wrapper.text()).toContain("16,000"));
    expect(wrapper.text()).toContain("CNY 0.0352");
    expect(wrapper.text()).toContain("OpenAI 官方");

    const buttons = wrapper.findAll(".segmented-control button");
    const btn30d = buttons.find((b) => b.text() === "最近 30 天");
    expect(btn30d).toBeDefined();
    await btn30d!.trigger("click");
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/runtime/tokens/30d"));
    wrapper.unmount();
    queryClient.clear();
  });
});
