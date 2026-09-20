import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AuthGate from "./AuthGate.vue";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("认证入口", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("匿名管理员可选择账号并进入验证码步骤", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(json({ admins: [{ qq: "10001", source: "plugin_admins" }] }))
      .mockResolvedValueOnce(json({ sent: true, message: "验证码已发送" }));
    const wrapper = mount(AuthGate);
    await vi.waitFor(() => expect(wrapper.text()).toContain("QQ 10001"));
    await wrapper.get("select").setValue("10001");
    await wrapper.get('button[type="submit"]').trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("六位验证码"));
    expect(fetcher.mock.calls[2]?.[0]).toBe("/personification/api/auth/login");
  });

  it("匿名管理员列表为空时仍提供刷新入口", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(json({ admins: [] }));
    const wrapper = mount(AuthGate);
    await vi.waitFor(() => expect(wrapper.text()).toContain("刷新管理员列表"));
    expect(wrapper.text()).toContain("暂未读取到可登录管理员");
  });
});
