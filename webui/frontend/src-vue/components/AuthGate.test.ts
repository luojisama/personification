import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@vue-app/stores/auth";
import AuthGate from "./AuthGate.vue";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("认证入口", () => {
  it("官方 QQ 领取显示请求命令而不声称已经发送", async () => {
    setActivePinia(createPinia());
    const auth = useAuthStore();
    auth.phase = 'anonymous';
    auth.admins = [{qq:'10001',source:'SUPERUSERS'}];
    const fetcher = vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(json({sent:false,message:'私聊发送：/登录 isolated-ticket'}));
    const wrapper = mount(AuthGate);
    await wrapper.findAll('select')[0]!.setValue('10001');
    await wrapper.findAll('select')[1]!.setValue('official');
    await wrapper.get('form').trigger('submit');
    await vi.waitFor(() => expect(wrapper.text()).toContain('/登录 isolated-ticket'));
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({qq:'10001',delivery:'official'});
    expect(wrapper.text()).not.toContain('验证码已发送');
    wrapper.unmount();
  });
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("匿名管理员可选择账号并进入验证码步骤", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(json({ detail: "No trusted device" }, 401))
      .mockResolvedValueOnce(json({ admins: [{ qq: "10001", source: "plugin_admins" }] }))
      .mockResolvedValueOnce(json({ sent: true, message: "验证码已发送" }));
    const wrapper = mount(AuthGate);
    await vi.waitFor(() => expect(wrapper.text()).toContain("QQ 10001"));
    await wrapper.get("select").setValue("10001");
    await wrapper.get('button[type="submit"]').trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("六位验证码"));
    expect(fetcher.mock.calls[3]?.[0]).toBe("/personification/api/auth/login");
  });

  it("匿名管理员列表为空时仍提供刷新入口", async () => {
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ detail: "Not authenticated" }, 401))
      .mockResolvedValueOnce(json({ detail: "No trusted device" }, 401))
      .mockResolvedValueOnce(json({ admins: [] }));
    const wrapper = mount(AuthGate);
    await vi.waitFor(() => expect(wrapper.text()).toContain("刷新管理员列表"));
    expect(wrapper.text()).toContain("暂未读取到可登录管理员");
  });

  it("Telegram 管理员显示投递平台，完整身份贯通选择、发送和验证", async () => {
    const qq = "telegram:6035198997";
    const auth = useAuthStore();
    auth.phase = "anonymous";
    auth.admins = [
      { qq: "10001", source: "SUPERUSERS" },
      { qq, source: "personification_telegram_admins", platform: "telegram", label: "Telegram 6035198997" },
    ];
    const fetcher = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json({ sent: true }))
      .mockResolvedValueOnce(json({ success: false, message: "验证码错误或已过期" }));
    const wrapper = mount(AuthGate);
    expect(wrapper.get('option[value="10001"]').text()).toBe("QQ 10001 · 超级用户");
    expect(wrapper.get(`option[value="${qq}"]`).text()).toBe("Telegram 6035198997 · 插件管理员");
    await wrapper.get("select").setValue(qq);
    expect(wrapper.text()).toContain("首次使用请先在 Telegram 私聊 Bot 发送 /start");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("六位验证码"));
    expect(wrapper.text()).toContain("登录账号：Telegram 6035198997");
    expect(wrapper.text()).toContain("请在 Telegram 与 Bot 的私聊中查看验证码");
    expect(wrapper.text()).not.toContain("QQ telegram:");
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("123456");
    await wrapper.get('input[autocomplete="off"]').setValue("TG 浏览器");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({ qq });
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toMatchObject({ qq, code: "123456", device_label: "TG 浏览器" });
    await vi.waitFor(() => expect(wrapper.text()).toContain("验证码错误或已过期"));
    expect(auth.phase).toBe("verifying");
    wrapper.unmount();
  });

  it("无平台元数据的 Telegram 身份仍可识别，发送失败展示中文提示", async () => {
    const auth = useAuthStore();
    auth.phase = "anonymous";
    auth.admins = [{ qq: "telegram:6035198997", source: "plugin_admins" }];
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json({ detail: "无法向该 Telegram 管理员发送私聊，请先向 Bot 发送 /start" }, 502));
    const wrapper = mount(AuthGate);
    expect(wrapper.text()).toContain("Telegram 6035198997 · 插件管理员");
    await wrapper.get("select").setValue("telegram:6035198997");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain("请先向 Bot 发送 /start"));
    expect(auth.phase).toBe("anonymous");
    wrapper.unmount();
  });

  it("未翻译的服务端登录错误使用中文兜底", async () => {
    const auth = useAuthStore();
    auth.phase = "anonymous";
    auth.admins = [{ qq: "10001", source: "plugin_admins" }];
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json({ detail: "private message delivery failed" }, 502));
    const wrapper = mount(AuthGate);
    await wrapper.get("select").setValue("10001");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toBe("操作未完成，请稍后重试。"));
    wrapper.unmount();
  });
  it("个人设备默认勾选，取消后验证码请求明确不信任", async () => {
    const auth = useAuthStore(); auth.phase = "verifying";
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ success: false }));
    const wrapper = mount(AuthGate);
    expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(true);
    await wrapper.get('input[type="checkbox"]').setValue(false);
    await wrapper.get('input[autocomplete="one-time-code"]').setValue("123456");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body)).trust_device).toBe(false);
    wrapper.unmount();
  });
});
