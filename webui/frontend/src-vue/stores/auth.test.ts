import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "./auth";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("认证状态", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("启动网络错误保持关门且允许重试", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(json({ qq: "10001", device_id: "dev", label: "PC", identity_source: "SUPERUSER" }));
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.phase).toBe("unavailable");
    expect(auth.identity).toBeNull();
    await auth.bootstrap();
    expect(auth.phase).toBe("authenticated");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("匿名状态只读取管理员列表，不自行输入或发送真实验证码", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(json({ admins: [{ qq: "10001", source: "plugin_admins" }], manual_entry: false }));
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.phase).toBe("anonymous");
    expect(auth.admins).toEqual([{ qq: "10001", source: "plugin_admins" }]);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("旧 bootstrap 响应不能覆盖更新的认证结果", async () => {
    let resolveOld!: (value: Response) => void;
    const old = new Promise<Response>((resolve) => { resolveOld = resolve; });
    vi.spyOn(globalThis, "fetch")
      .mockReturnValueOnce(old)
      .mockResolvedValueOnce(json({ qq: "new", device_id: "new-dev", label: "new", identity_source: "plugin_admin" }));
    const auth = useAuthStore();
    const first = auth.bootstrap();
    const second = auth.bootstrap();
    await second;
    resolveOld(json({ qq: "old", device_id: "old-dev", label: "old", identity_source: "SUPERUSER" }));
    await first;
    expect(auth.identity?.qq).toBe("new");
  });

  it("退出后晚到的验证码验证响应不能重新开门", async () => {
    let resolveVerify!: (value: Response) => void;
    const pendingVerify = new Promise<Response>((resolve) => { resolveVerify = resolve; });
    vi.spyOn(globalThis, "fetch")
      .mockReturnValueOnce(pendingVerify)
      .mockResolvedValueOnce(json({ success: true }))
      .mockResolvedValueOnce(json({ admins: [] }));
    const auth = useAuthStore();
    auth.phase = "verifying";
    const verifying = auth.verify("10001", "123456", "PC");
    const logout = auth.logout();
    expect(auth.phase).toBe("anonymous");
    resolveVerify(json({ success: true }));
    await Promise.all([verifying, logout]);
    expect(auth.phase).toBe("anonymous");
    expect(auth.identity).toBeNull();
  });
});
