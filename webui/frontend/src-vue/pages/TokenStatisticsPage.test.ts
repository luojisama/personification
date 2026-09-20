import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { SubscriptionQuotaResponse } from "@/api/types";
import { tokenBillingApi, type TokenUsageResponse } from "@/api/tokenBilling";
import TokenStatisticsPage from "./TokenStatisticsPage.vue";

vi.mock("@/api/resources", () => ({
  resources: { metrics: vi.fn(), subscriptionQuotas: vi.fn() },
}));
vi.mock("@/api/tokenBilling", () => ({
  tokenBillingApi: { usage: vi.fn(), prices: vi.fn(), repricePreview: vi.fn() },
}));
vi.mock("@vue-app/components/TokenUsageChart.vue", () => ({
  default: {
    props: ["data"],
    template: '<div data-testid="usage-chart">usage chart</div>',
  },
}));
vi.mock("@vue-app/components/TokenPriceEditor.vue", () => ({
  default: { template: '<div data-testid="price-editor">price editor</div>' },
}));

const mockTokenSummary: TokenUsageResponse = {
  window: "24h",
  filters: {},
  input_tokens: 12000,
  output_tokens: 4000,
  total_tokens: 16000,
  call_count: 20,
  cache_read_tokens: null,
  cache_creation_tokens: 500,
  cache_read_known_calls: 0,
  cache_creation_known_calls: 5,
  cache_usage_complete_calls: 5,
  cache_usage_coverage: 0.25,
  cache_read_input_ratio: null,
  costs: [{ currency: "CNY", cost_decimal: "0.0352", priced_call_count: 18 }],
  unpriced_call_count: 2,
  incomplete_priced_call_count: 1,
  legacy_unattributed: 1,
  series: [
    {
      bucket: "2026-09-19",
      input_tokens: 6000,
      output_tokens: 2000,
      total_tokens: 8000,
      call_count: 10,
    },
    {
      bucket: "2026-09-20",
      input_tokens: 6000,
      output_tokens: 2000,
      total_tokens: 8000,
      call_count: 10,
    },
  ],
  recent_events: [
    {
      event_id: "evt-1",
      observed_at: 1700000000,
      route_id: "route-openai",
      provider: "openai",
      model: "gpt-4o",
      purpose: "main",
      bot_id: "bot-a",
      group_id: "group-a",
      input_tokens: 12000,
      output_tokens: 4000,
      total_tokens: 16000,
      cache_read_tokens: null,
      cache_creation_tokens: 500,
      price_version_id: "price-1",
      currency: "CNY",
      cost_decimal: "0.0352",
      pricing_complete: true,
    },
  ],
  dimensions: {
    bot_ids: ["bot-a"],
    group_ids: ["group-a"],
    providers: ["openai"],
    route_ids: ["route-openai"],
    models: ["gpt-4o"],
    purposes: ["main"],
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
    vi.mocked(resources.metrics).mockResolvedValue({
      window: "day",
      generated_at: 1700000000000,
      total: {
        prompt_tokens: 12000,
        completion_tokens: 4000,
        total_tokens: 16000,
        call_count: 20,
      },
      series: [],
      by_model: [],
      by_group: [],
      by_purpose: [],
      provider_usage: [],
      billing: {
        cost_configured: false,
        currency: "",
        request_cost: 0,
        note: "旧账本",
      },
    });
    vi.mocked(tokenBillingApi.usage).mockResolvedValue(mockTokenSummary);
    vi.mocked(tokenBillingApi.prices).mockResolvedValue({ items: [] });
    vi.mocked(resources.subscriptionQuotas).mockResolvedValue(emptyQuota);
  });

  async function renderPage(initialPath = "/runtime/tokens/24h") {
    const pinia = createPinia();
    setActivePinia(pinia);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/runtime/tokens/:window", component: TokenStatisticsPage },
      ],
    });
    await router.push(initialPath);
    await router.isReady();

    const wrapper = mount(TokenStatisticsPage, {
      global: { plugins: [pinia, [VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, router, queryClient };
  }

  it("由路由参数权威驱动时间窗口并渲染 Token 统计与费用", async () => {
    const { wrapper, router, queryClient } =
      await renderPage("/runtime/tokens/7d");
    await vi.waitFor(() => expect(wrapper.text()).toContain("16,000"));
    expect(wrapper.text()).toContain("CNY");
    expect(wrapper.text()).toContain("0.0352");
    expect(wrapper.text()).toContain("缓存读取未知");

    const buttons = wrapper.findAll(".segmented-control button");
    const btn30d = buttons.find((b) => b.text() === "最近 30 天");
    expect(btn30d).toBeDefined();
    await btn30d!.trigger("click");
    await vi.waitFor(() =>
      expect(router.currentRoute.value.path).toBe("/runtime/tokens/30d"),
    );
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
