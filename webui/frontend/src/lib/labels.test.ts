import { describe, expect, it } from "vitest";

import { traceDeliveryStatusLabel, traceHistoryStatusLabel, traceOutcomeLabel } from "./labels";


describe("Trace 状态文案", () => {
  it("不会把未回复或未知状态呈现为成功", () => {
    expect(traceOutcomeLabel("no_reply")).toBe("未发送可见回复");
    expect(traceOutcomeLabel("unknown")).toBe("结果未知");
    expect(traceDeliveryStatusLabel("unconfirmed")).toContain("请勿直接重试");
    expect(traceDeliveryStatusLabel("unexpected")).toBe("未知状态（unexpected）");
    expect(traceHistoryStatusLabel("skipped")).toBe("未提交");
  });
});
