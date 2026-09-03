import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { AgentRuntimeSnapshot } from "@/api/types";
import { useBotStore } from "@vue-app/stores/bot";
import AgentStatusPage from "./AgentStatusPage.vue";

vi.mock("@/api/resources", () => ({ resources: { agentRuntime: vi.fn() } }));

const mockSnapshot: AgentRuntimeSnapshot = {
  bot: {
    bot_id: "10001",
    nickname: "测试 Bot",
    avatar_url: null,
    online: true,
    is_default: true,
    last_seen_at: 1700000000000,
  },
  connected_bots: [],
  enabled: true,
  running: true,
  last_active_at: 1700000000000,
  waiting_turns: 0,
  admission_waiting_turns: 1,
  buffered_sessions: 2,
  buffered_messages: 5,
  processing_buffer_sessions: 0,
  oldest_buffer_age_ms: 1500,
  next_buffer_fire_ms: 200,
  active_turns: 2,
  sending_turns: 1,
  gated_turns: 0,
  cancelled_turns: 0,
  stale_turns: 0,
  event_loop_p50_ms: 2,
  event_loop_p95_ms: 8,
  turn_p50_ms: 600,
  turn_p95_ms: 1400,
  rss_bytes: 150 * 1024 * 1024,
  peak_rss_bytes: 200 * 1024 * 1024,
  background_tasks: 4,
  background_failures: 0,
  cache_entries: 128,
  inner_state: { mood: "平静", energy: "高", pending_count: 0, updated_at: "12:00:00" },
  recent_traces: [
    {
      trace_id: "tr_agent_1",
      state: "finished",
      outcome: "ok",
      updated_at: 1700000000000,
      elapsed_ms: 750,
      model: "gpt-4o",
      tool_count: 2,
      session_type: "group",
      group_id: "123456",
      diagnosis_code: "ok",
    },
  ],
  generated_at: 1700000000000,
};

describe("AgentStatusPage", () => {
  beforeEach(() => {
    vi.mocked(resources.agentRuntime).mockResolvedValue(mockSnapshot);
  });

  async function renderPage() {
    const pinia = createPinia();
    setActivePinia(pinia);
    const botStore = useBotStore();
    botStore.setBotId("10001");
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/agent/:section", component: AgentStatusPage }],
    });
    await router.push("/runtime/agent/status");
    await router.isReady();

    const wrapper = mount(AgentStatusPage, {
      global: { plugins: [pinia, [VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, queryClient };
  }

  it("正确渲染 Agent 身份、指标机架及内存统计", async () => {
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("测试 Bot"));
    expect(wrapper.text()).toContain("协议端在线");
    expect(wrapper.text()).toContain("150.0 MB");
    wrapper.unmount();
    queryClient.clear();
  });
});
