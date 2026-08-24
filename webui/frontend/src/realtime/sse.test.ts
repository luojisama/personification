import { describe, expect, it, vi } from "vitest";

import { parseSseBlock, RuntimeEventClient } from "./sse";

describe("SSE 客户端", () => {
  it("解析事件、游标和多行数据", () => {
    expect(
      parseSseBlock('id: 8\nevent: turn.stage\ndata: {"id":8,\ndata: "topic":"turn.stage"}'),
    ).toEqual({
      id: "8",
      event: "turn.stage",
      data: '{"id":8,\n"topic":"turn.stage"}',
    });
    expect(parseSseBlock(": heartbeat")).toBeNull();
  });

  it("停止后释放连接并报告关闭", () => {
    const onState = vi.fn();
    const client = new RuntimeEventClient({
      onEvent: vi.fn(),
      onResync: vi.fn(),
      onState,
      storage: { getItem: () => "0", setItem: vi.fn() },
    });
    client.stop();
    expect(onState).toHaveBeenCalledWith("closed");
  });
});
