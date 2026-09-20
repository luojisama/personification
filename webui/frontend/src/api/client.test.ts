import { describe, expect, it, vi } from "vitest";

import { apiRequest, onAuthenticationInvalid, rawApiRequest } from "./client";

describe("API 认证失效通知", () => {
  it("已取消的旧请求即使晚到 401 也不会使新会话失效", async () => {
    const listener = vi.fn();
    const remove = onAuthenticationInvalid(listener);
    const controller = new AbortController();
    controller.abort();
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ detail: "expired" }), {
      status: 401, headers: { "content-type": "application/json" },
    }));
    await expect(apiRequest("/old", { signal: controller.signal })).rejects.toMatchObject({ status: 401 });
    await expect(rawApiRequest("/personification/api/v2", "/old", "", {}, controller.signal)).rejects.toMatchObject({ status: 401 });
    expect(listener).not.toHaveBeenCalled();
    remove();
  });
});
