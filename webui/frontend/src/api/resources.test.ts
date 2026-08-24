import { describe, expect, it } from "vitest";

import { sanitizeTraceDetail } from "./resources";

describe("Trace 白名单 DTO", () => {
  it("忽略隐藏推理、密钥和未知字段", () => {
    const trace = sanitizeTraceDetail({
      trace_id: "trace-1",
      user_id: "10001",
      chain_of_thought: "不应出现",
      api_key: "secret",
      decision: { action: "observe", reason_code: "agent_observe", prompt: "secret" },
      stages: [{ key: "attention", label: "注意力", status: "ok", summary: "结构化摘要" }],
      tools: [{ name: "web_search", status: "ok", raw_result: "secret" }],
    });

    expect(trace.trace_id).toBe("trace-1");
    expect(trace.decision).toEqual(
      expect.objectContaining({ action: "observe", reason_code: "agent_observe" }),
    );
    expect(trace).not.toHaveProperty("chain_of_thought");
    expect(trace).not.toHaveProperty("api_key");
    expect(trace.decision).not.toHaveProperty("prompt");
    expect(trace.tools[0]).not.toHaveProperty("raw_result");
  });
});
