import { createPinia, setActivePinia, getActivePinia, disposePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, notifyAuthenticationInvalid } from "@/api/client";
import { useAuthStore } from "./auth";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("认证状态", () => {
  beforeEach(() => {
    if (getActivePinia()) disposePinia(getActivePinia()!);
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
      .mockResolvedValueOnce(json({ detail: "No trusted device" }, 401))
      .mockResolvedValueOnce(json({ admins: [{ qq: "10001", source: "plugin_admins" }], manual_entry: false }));
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.phase).toBe("anonymous");
    expect(auth.admins).toEqual([{ qq: "10001", source: "plugin_admins" }]);
    expect(fetcher).toHaveBeenCalledTimes(3);
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
  it("受信任浏览器仅恢复一次并重新读取身份", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ detail: "expired" }, 401))
      .mockResolvedValueOnce(json({ success: true }))
      .mockResolvedValueOnce(json({ qq: "10001", device_id: "dev", trusted: true }));
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.phase).toBe("authenticated");
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/personification/api/auth/me", "/personification/api/auth/refresh", "/personification/api/auth/me"]);
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get("X-Personification-Refresh")).toBe("1");
  });

  it("恢复网络失败保持服务不可用，403不尝试恢复", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({}, 401)).mockRejectedValueOnce(new TypeError("offline"));
    const auth = useAuthStore();
    await auth.bootstrap();
    expect(auth.phase).toBe("unavailable");
    fetcher.mockReset().mockResolvedValueOnce(json({ detail: "forbidden" }, 403)).mockResolvedValueOnce(json({ admins: [] }));
    await auth.bootstrap();
    expect(auth.phase).toBe("anonymous");
    expect(fetcher.mock.calls.map(call => call[0])).not.toContain("/personification/api/auth/refresh");
  });

  it("并发失效只发起一次恢复，晚到恢复不能覆盖退出", async () => {
    let resolveRefresh!: (value: Response) => void;
    const refresh = new Promise<Response>(resolve => { resolveRefresh = resolve; });
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({}, 401)).mockReturnValueOnce(refresh);
    const auth = useAuthStore();
    auth.phase = "authenticated";
    notifyAuthenticationInvalid(new ApiError(401, {}));
    notifyAuthenticationInvalid(new ApiError(401, {}));
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    fetcher.mockResolvedValueOnce(json({ success: true })).mockResolvedValueOnce(json({ admins: [] }));
    await auth.logout();
    resolveRefresh(json({ success: true }));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(auth.phase).toBe("anonymous");
    expect(auth.identity).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("主动退出后重新检查也不会以信任凭证自动复活", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json({ success: true })).mockResolvedValueOnce(json({ admins: [] }));
    const auth = useAuthStore();
    await auth.logout();
    fetcher.mockReset().mockResolvedValueOnce(json({ admins: [] }));
    await auth.bootstrap();
    expect(auth.phase).toBe("anonymous");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

});
