import { describe, expect, it } from "vitest";

import { sanitizeTraceDetail, sanitizeTraceListItem, sanitizeTracePage } from "./resources";

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

  it("保留运行时真实 outcome，并对白名单列表字段做同一套清理", () => {
    const noReply = sanitizeTraceListItem({
      trace_id: "trace-no-reply",
      outcome: "no_reply",
      input_summary: "收到的消息",
      hidden_prompt: "不应出现",
    });
    const finished = sanitizeTraceDetail({
      trace_id: "trace-finished",
      outcome: "finished",
      input_summary: "另一条消息",
    });

    expect(noReply.outcome).toBe("no_reply");
    expect(noReply.input_summary).toBe("收到的消息");
    expect(noReply).not.toHaveProperty("hidden_prompt");
    expect(finished.outcome).toBe("finished");
  });

  it("对白名单分页外壳做严格投影并丢弃未知顶层字段", () => {
    const page = sanitizeTracePage({
      items: [{ trace_id: "trace-page", hidden_prompt: "不应出现" }],
      page: 2.8,
      page_size: 20,
      total: 21,
      total_pages: 2,
      secret: "不应出现",
    });

    expect(page).toEqual({
      items: [expect.objectContaining({ trace_id: "trace-page" })],
      page: 2,
      page_size: 20,
      total: 21,
      total_pages: 2,
    });
    expect(page).not.toHaveProperty("secret");
    expect(page.items[0]).not.toHaveProperty("hidden_prompt");
  });
});
