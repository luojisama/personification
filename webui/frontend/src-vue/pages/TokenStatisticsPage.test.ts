import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { SubscriptionQuotaResponse, TokenSummary } from "@/api/types";
import TokenStatisticsPage from "./TokenStatisticsPage.vue";

vi.mock("@/api/resources", () => ({ resources: { metrics: vi.fn(), subscriptionQuotas: vi.fn() } }));

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
      source: "local_token_ledger",
    },
  ],
  billing: {
    cost_configured: true,
    currency: "CNY",
    request_cost: 0.0352,
    note: "按标准倍率计算",
  },
};

const emptyQuota: SubscriptionQuotaResponse = {
  items: [],
  checked_at: 1700000000,
  cache_ttl_seconds: 60,
  force_cooldown_seconds: 30,
  diagnostic_code: "subscription_quota_not_configured",
};

describe("TokenStatisticsPage", () => {
  beforeEach(() => {
    vi.mocked(resources.metrics).mockResolvedValue(mockTokenSummary);
    vi.mocked(resources.subscriptionQuotas).mockResolvedValue(emptyQuota);
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

  it("仅在订阅代理返回窗口时展示真实额度", async () => {
    vi.mocked(resources.subscriptionQuotas).mockResolvedValue({
      ...emptyQuota,
      diagnostic_code: "subscription_quota_routes_available",
      items: [
        {
          route_name: "主订阅路由",
          route_fingerprint: "route-one",
          status: "available",
          diagnostic_code: "subscription_quota_available",
          checked_at: 1700000000,
          source: "codex_wham_proxy",
          cached: false,
          windows: [
            {
              window_type: "five_hour",
              limit_window_seconds: 18000,
              used_percent: 35,
              remaining_percent: 65,
              reset_at: null,
            },
          ],
        },
      ],
    });
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("订阅窗口额度"));
    expect(wrapper.text()).toContain("五小时窗口：已用 35.0%，剩余 65.0%");
    expect(wrapper.find("progress").attributes("value")).toBe("35");
    wrapper.unmount();
    queryClient.clear();
  });
});
