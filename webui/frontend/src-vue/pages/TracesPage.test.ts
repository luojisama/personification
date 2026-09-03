import { describe, expect, it } from "vitest";
import type { TraceDetail } from "@/api/types";
import { deriveTraceMetrics, traceTriageText } from "./tracesPageMetrics";

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
});
