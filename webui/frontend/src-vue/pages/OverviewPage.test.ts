import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { ref, shallowRef } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { OverviewSnapshot } from "@/api/types";
import { RUNTIME_EVENTS_KEY, type RuntimeEventsManager } from "@vue-app/realtime/runtimeEvents";
import OverviewPage from "./OverviewPage.vue";

vi.mock("@/api/resources", () => ({ resources: { overview: vi.fn() } }));

const mockOverview: OverviewSnapshot = {
  generated_at: "2026-03-31T09:00:00.000Z",
  runtime_status: "healthy",
  active_turns: 3,
  events_last_hour: 42,
  p95_turn_ms: 1250,
  route_counts: { supported: 8, unknown: 1, unsupported: 0 },
  recovery_counts: { pending: 2, processing: 0, quarantined: 1, expired: 0 },
  latest_traces: [
    {
      trace_id: "tr_12345678abcdef",
      started_at: "2026-03-31T08:59:00.000Z",
      finished_at: null,
      session_type: "group",
      group_id: "group_1",
      user_id: "user_1",
      user_name: "测试人员",
      avatar_url: null,
      outcome: "ok",
      diagnosis_code: "turn_success",
      input_summary: "ping",
      elapsed_ms: 800,
    },
  ],
  diagnostics: [
    {
      code: "diag_mock_1",
      title: "模型响应轻微抖动",
      level: "warn",
      trace_id: "tr_12345678abcdef",
    },
  ],
};

function runtimeManager(): RuntimeEventsManager {
  return {
    events: shallowRef([]),
    state: ref("open"),
    resyncCount: ref(0),
    client: shallowRef(null),
    start: vi.fn(),
    stop: vi.fn(),
  };
}

describe("OverviewPage", () => {
  beforeEach(() => {
    vi.mocked(resources.overview).mockResolvedValue(mockOverview);
  });

  async function renderPage() {
    const pinia = createPinia();
    setActivePinia(pinia);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/overview/summary", component: OverviewPage }],
    });
    await router.push("/runtime/overview/summary");
    await router.isReady();

    const wrapper = mount(OverviewPage, {
      global: {
        plugins: [pinia, [VueQueryPlugin, { queryClient }], router],
        provide: { [RUNTIME_EVENTS_KEY as symbol]: runtimeManager() },
      },
    });
    return { wrapper, queryClient };
  }

  it("正确渲染概览指标、实时状态与 Trace 表格", async () => {
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("健康"));
    expect(wrapper.text()).toContain("SSE 在线");
    expect(wrapper.text()).toContain("测试人员");
    expect(wrapper.text()).toContain("模型响应轻微抖动");
    wrapper.unmount();
    queryClient.clear();
  });
});
