import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { resources } from "@/api/resources";
import type { TraceDetail, TraceListItem } from "@/api/types";
import { deriveTraceMetrics, finalReviewDisplay, stageDisplayLabel, stageDisplaySummary, traceTriageText } from "./tracesPageMetrics";
import TracesPage from "./TracesPage.vue";

vi.mock("@/api/resources", () => ({ resources: { traces: vi.fn(), trace: vi.fn() } }));

const trace: TraceDetail = {
  trace_id: "trace-safe",
  started_at: "2026-08-31T10:00:00+08:00",
  finished_at: "2026-08-31T10:00:02+08:00",
  session_type: "private",
  group_id: null,
  user_id: "masked-user",
  user_name: "测试用户",
  avatar_url: null,
  outcome: "failed",
  diagnosis_code: "provider_request_rejected",
  input_summary: "脱敏输入摘要",
  elapsed_ms: 2000,
  bot_id: "masked-bot",
  media_summary: [],
  decision: { summary: "回答插件问题", action: "answer", tier: 1, wait_seconds: 0, interest: 0.9, reason_code: "direct" },
  stages: [
    { key: "agent_tool_result", label: "知识库结果", status: "ok", started_at: null, finished_at: null, duration_ms: 80, summary: "result_len=100", detail_code: "agent_tool_result", remaining_ms: null },
    { key: "slow_warning", label: "慢阶段", status: "warn", started_at: null, finished_at: null, duration_ms: 900, summary: "timeout=true", detail_code: "slow_warning", remaining_ms: null },
    { key: "provider_failure", label: "Provider 调用失败", status: "error", started_at: null, finished_at: null, duration_ms: 20, summary: "code=provider_request_rejected route_1=provider:x|upstream:INVALID_ARGUMENT/function_response_mismatch", detail_code: "provider_failure", remaining_ms: null },
  ],
  tools: [
    { name: "工具 Schema 兼容处理", namespace: "runtime", status: "ok", duration_ms: 5, argument_summary: "", result_summary: "tools=2/2", schema_hash: "safe", detail_code: "call" },
    { name: "search_plugin_knowledge", namespace: "runtime", status: "ok", duration_ms: 80, argument_summary: "", result_summary: "result_len=100", schema_hash: "", detail_code: "result" },
    { name: "list_plugins", namespace: "runtime", status: "ok", duration_ms: 10, argument_summary: "", result_summary: "result_len=20", schema_hash: "", detail_code: "result" },
  ],
  final_reply: "",
  send_status: "not_started",
  history_status: "unknown",
  recovery_ids: [],
};

describe("Vue Traces metrics & triage logic", () => {
  it("derives first error, slow stages and allowlisted upstream classification", () => {
    const metrics = deriveTraceMetrics(trace);
    expect(metrics).toEqual({
      issueCount: 2,
      completedToolCount: 2,
      firstErrorIndex: 2,
      slowStageIndexes: [1, 0, 2],
      upstreamStatus: "INVALID_ARGUMENT",
      upstreamDetailCode: "function_response_mismatch",
    });
    const text = traceTriageText(trace, metrics);
    expect(text).toContain("已确认成功返回 2 条工具结果");
    expect(text).toContain("INVALID_ARGUMENT / function_response_mismatch");
  });

  it("sanitizes unallowlisted upstream format", () => {
    const injected: TraceDetail = {
      ...trace,
      stages: trace.stages.map((stage) => stage.key === "provider_failure"
        ? { ...stage, summary: "upstream:<script>/secret value" }
        : stage),
    };
    const metrics = deriveTraceMetrics(injected);
    expect(metrics.upstreamStatus).toBe("");
    expect(metrics.upstreamDetailCode).toBe("");
  });

  it("does not describe no-reply or unknown outcomes as successful", () => {
    const noReply: TraceDetail = {
      ...trace,
      outcome: "no_reply",
      diagnosis_code: "policy_silence",
      stages: [],
    };
    const unknown: TraceDetail = {
      ...trace,
      outcome: "unknown",
      diagnosis_code: "delivery_unknown",
      stages: [],
    };

    expect(traceTriageText(noReply, deriveTraceMetrics(noReply))).toContain("没有发送可见回复");
    expect(traceTriageText(unknown, deriveTraceMetrics(unknown))).toContain("最终结果无法确认");
  });

  it("maps the controlled final-review protocol without exposing its raw summary", () => {
    const reviewStage = {
      key: "final_review_decision",
      label: "旧版终审结论",
      status: "warn" as const,
      started_at: null,
      finished_at: null,
      duration_ms: 123,
      summary: "reason=review_timeout action=no_reply source=initial available_evidence_fields=3 provider_text=ignore-me",
      detail_code: "review_timeout",
      remaining_ms: 42,
    };
    expect(stageDisplayLabel(reviewStage)).toBe("最终审阅结论");
    expect(finalReviewDisplay(reviewStage)).toMatchObject({
      reasonLabel: "终审请求超时",
      actionLabel: "不发送可见回复",
      sourceLabel: "初审",
      availableEvidenceFields: 3,
    });
  });

  it("keeps old Trace stage labels and gives completed media evidence its delivery boundary", () => {
    const oldStage = { ...trace.stages[0]!, key: "agent_tool_result", label: "旧版工具阶段", detail_code: "result" };
    const mediaComplete = {
      ...trace.stages[0]!, key: "agent_reply_quality", label: "Agent 回复质量", detail_code: "agent_reply_quality",
      summary: "action=accepted source=- available_evidence_fields=3 media_delivery=complete elapsed_ms=12",
    };
    const untrustedToolStage = { ...trace.stages[0]!, key: "agent_tool_result", label: "工具结果", summary: "media_delivery=complete" };
    expect(stageDisplayLabel(oldStage)).toBe("旧版工具阶段");
    expect(stageDisplaySummary(oldStage)).toBe("result_len=100");
    expect(stageDisplayLabel(mediaComplete)).toBe("Agent 媒体证据检查通过");
    expect(stageDisplaySummary(mediaComplete)).toContain("不表示 QQ 已送达");
    expect(stageDisplayLabel(untrustedToolStage)).toBe("工具结果");
  });
});

const listItem: TraceListItem = {
  trace_id: "trace-first", started_at: "2026-09-06T03:30:00Z", finished_at: null,
  session_type: "group", group_id: "30001", user_id: "20001", user_name: "合成用户",
  avatar_url: null, outcome: "ok", diagnosis_code: "synthetic", input_summary: "合成摘要", elapsed_ms: 10,
};

async function renderTracePage(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/runtime/traces/index", component: TracesPage },
      { path: "/runtime/traces/timeline", component: TracesPage },
      { path: "/runtime/traces/timeline/:traceId", component: TracesPage },
    ],
  });
  await router.push(path); await router.isReady();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = mount(TracesPage, { global: { plugins: [router, [VueQueryPlugin, { queryClient }]] } });
  await vi.waitFor(() => expect(vi.mocked(resources.traces)).toHaveBeenCalled());
  return { router, wrapper, queryClient };
}

describe("Trace index route behavior", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(resources.traces).mockResolvedValue({ items: [listItem], page: 1, page_size: 20, total: 2, total_pages: 2 });
    vi.mocked(resources.trace).mockResolvedValue(trace);
  });

  it("keeps old-link index landing on the index instead of auto-selecting a Trace", async () => {
    const { router, wrapper, queryClient } = await renderTracePage("/runtime/traces/index?source=legacy#proof");
    await vi.waitFor(() => expect(wrapper.text()).toContain("合成用户"));
    expect(router.currentRoute.value.fullPath).toBe("/runtime/traces/index?source=legacy#proof");
    wrapper.unmount(); queryClient.clear();
  });

  it("only auto-selects from the explicit timeline and preserves query/hash", async () => {
    const { router, wrapper, queryClient } = await renderTracePage("/runtime/traces/timeline?source=timeline#proof");
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe("/runtime/traces/timeline/trace-first?source=timeline#proof"));
    wrapper.unmount(); queryClient.clear();
  });

  it("resets the list page as soon as a search changes", async () => {
    const { wrapper, queryClient } = await renderTracePage("/runtime/traces/index");
    await vi.waitFor(() => expect(wrapper.find("button[aria-label='下一页']").exists()).toBe(true));
    await wrapper.get("button[aria-label='下一页']").trigger("click");
    await vi.waitFor(() => expect(vi.mocked(resources.traces)).toHaveBeenLastCalledWith(2, 20, "", expect.anything()));
    await wrapper.get("input[type='search']").setValue("trace-first");
    await vi.waitFor(() => expect(vi.mocked(resources.traces)).toHaveBeenLastCalledWith(1, 20, "", expect.anything()));
    wrapper.unmount(); queryClient.clear();
  });

  it("renders a Chinese final-review diagnostic and keeps QQ delivery separate", async () => {
    vi.mocked(resources.trace).mockResolvedValue({
      ...trace,
      stages: [
        {
          key: "agent_reply_quality", label: "Agent 回复质量", status: "ok", started_at: null, finished_at: null,
          duration_ms: 12, summary: "action=accepted source=- available_evidence_fields=3 media_delivery=complete elapsed_ms=12", detail_code: "agent_reply_quality", remaining_ms: null,
        },
        {
          key: "final_review_decision", label: "旧终审", status: "warn", started_at: null, finished_at: null,
          duration_ms: 321, summary: "reason=review_model_no_reply action=no_reply source=verification available_evidence_fields=5", detail_code: "review_model_no_reply", remaining_ms: null,
        },
      ],
    });
    const { wrapper, queryClient } = await renderTracePage("/runtime/traces/timeline/trace-safe");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Agent 媒体证据检查通过"));
    expect(wrapper.text()).toContain("这不表示 QQ 已送达");
    expect(wrapper.text()).toContain("终审模型选择不回复");
    expect(wrapper.text()).toContain("独立复核");
    expect(wrapper.text()).toContain("可用媒体证据字段");
    expect(wrapper.text()).not.toContain("reason=review_model_no_reply");
    wrapper.unmount(); queryClient.clear();
  });
});
