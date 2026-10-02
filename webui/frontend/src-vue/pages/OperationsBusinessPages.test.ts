import { describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";

import OperationsBusinessPages from "../pages/OperationsBusinessPages.vue";
import { resources } from "@/api/resources";
import { useAuthStore } from "@vue-app/stores/auth";
import { useBotStore } from "@vue-app/stores/bot";

vi.mock("@/api/resources", () => ({
  resources: {
    userPolicyStates: vi.fn().mockResolvedValue({
      states: [
        { user_id: "10001", tier: "blocked", source: "admin", revision: 2, expires_at: null },
      ],
    }),
    userPolicyEvents: vi.fn().mockResolvedValue({ events: [] }),
    updateUserPolicy: vi.fn().mockResolvedValue({ ok: true, code: "policy_updated" }),
    outboundRecent: vi.fn().mockResolvedValue({
      messages: [
        { operation_id: "op_12345", bot_id: "20001", conversation_kind: "group", conversation_id: "30001", status: "succeeded", trace_id: "tr_abc" },
      ],
    }),
    recallOutbound: vi.fn().mockResolvedValue({ ok: true, code: "outbound_recalled" }),
    auditActions: vi.fn().mockResolvedValue({ actions: [{ key: "ban", label: "封禁用户" }] }),
    auditRecent: vi.fn().mockResolvedValue({
      entries: [
        { id: 1, ts: Date.now(), action: "ban", qq: "10000", target: "10001", outcome: "succeeded" },
      ],
    }),
    qqGet: vi.fn().mockResolvedValue({ user_id: "20001", nickname: "Bot Assistant", groups: [] }),
    qqPost: vi.fn().mockResolvedValue({ ok: true, code: "qq_updated" }),
    qqDelete: vi.fn().mockResolvedValue({ ok: true, code: "qq_deleted" }),
    deviceGet: vi.fn().mockResolvedValue({ current_device_id: "dev_1", devices: [{ id: "dev_1", label: "Edge/Win", status: "active" }] }),
    devicePost: vi.fn().mockResolvedValue({ ok: true, code: "device_approved" }),
    deviceDelete: vi.fn().mockResolvedValue({ ok: true, code: "device_revoked" }),
    createStateExport: vi.fn().mockResolvedValue({ ok: true, task_id: "task_exp" }),
    uploadStateImport: vi.fn().mockResolvedValue({ ok: true, task_id: "task_imp" }),
    inspectImport: vi.fn().mockResolvedValue({ schema_version: "v2", bot_id: "20001", group_id: "30001" }),
    dryRunImport: vi.fn().mockResolvedValue({ plan_token: "ptok_999" }),
    applyImport: vi.fn().mockResolvedValue({ journal_id: "jrn_777" }),
    rollbackImport: vi.fn().mockResolvedValue({ ok: true }),
    groupsFiltered: vi.fn().mockResolvedValue({
      items: [{
        group_id: "30001",
        group_name: "迁移测试群",
        avatar_url: null,
        enabled: true,
        membership_state: "confirmed",
        bot_ids: ["20001"],
        bot_self_ids: ["20001"],
        sources: [],
        member_count: null,
        last_active_at: null,
        freshness: 1,
        cache_only: false,
      }],
      page: 1,
      page_size: 100,
      total: 1,
      total_pages: 1,
    }),
  },
}));

function createTestSetup(initialRoute: string, mode: string) {
  const pinia = createPinia();
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: "/operations/:category/:section",
        component: OperationsBusinessPages,
        meta: { mode },
      },
    ],
  });
  router.push(initialRoute);
  return { pinia, queryClient, router };
}

describe("OperationsBusinessPages.vue", () => {
  it("renders User Policies page with table records", async () => {
    const { pinia, queryClient, router } = createTestSetup("/operations/user-policies/list", "user-policies");
    await router.isReady();

    const wrapper = mount(OperationsBusinessPages, {
      props: { mode: "user-policies" },
      global: {
        plugins: [pinia, [VueQueryPlugin, { queryClient }], router],
      },
    });

    expect(wrapper.text()).toContain("用户策略与黑名单");
    expect(resources.userPolicyStates).toHaveBeenCalled();
  });

  it("renders Outbound Messages page with ledger entries", async () => {
    const { pinia, queryClient, router } = createTestSetup("/operations/outbound/list", "outbound");
    await router.isReady();

    const wrapper = mount(OperationsBusinessPages, {
      props: { mode: "outbound" },
      global: {
        plugins: [pinia, [VueQueryPlugin, { queryClient }], router],
      },
    });

    expect(wrapper.text()).toContain("近期 Bot 消息");
    expect(resources.outboundRecent).toHaveBeenCalled();
  });

  it("uses the current Bot group selector for data transfer targets", async () => {
    const { pinia, queryClient, router } = createTestSetup("/operations/data-transfer/export?group_id=30001", "data-transfer");
    await router.isReady();
    useBotStore(pinia).setBotId("20001");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const wrapper = mount(OperationsBusinessPages, {
      props: { mode: "data-transfer" },
      global: {
        plugins: [pinia, [VueQueryPlugin, { queryClient }], router],
      },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("数据迁移");
    expect(wrapper.text()).toContain("目标群");
    expect(wrapper.text()).not.toContain("群 ID");
    expect(wrapper.find("button.button-primary").text()).toBe("创建导出");
    await wrapper.find("button.button-primary").trigger("click");
    expect(confirmSpy).toHaveBeenCalled();
    expect(resources.createStateExport).toHaveBeenCalledWith({
      bot_id: "20001",
      group_id: "30001",
      datasets: [],
    });
  });
  it("当前设备可开启信任，受信任记录可撤销且旧记录标为失效", async () => {
    const { pinia, queryClient, router } = createTestSetup("/operations/devices/current", "devices");
    await router.isReady();
    const auth = useAuthStore(pinia);
    const bootstrap = vi.spyOn(auth, "bootstrap").mockResolvedValue();
    const wrapper = mount(OperationsBusinessPages, { props: { mode: "devices" }, global: { plugins: [pinia, [VueQueryPlugin, { queryClient }], router] } });
    await flushPromises();
    const trust = wrapper.findAll("button").find(button => button.text() === "信任当前设备")!;
    await trust.trigger("click"); await flushPromises();
    expect(resources.devicePost).toHaveBeenCalledWith("devices/dev_1/trust");
    expect(bootstrap).toHaveBeenCalled();
    vi.mocked(resources.deviceGet).mockResolvedValueOnce({ devices: [{ id: "legacy_1", label: "旧记录", valid: false }] });
    await router.push("/operations/devices/trusted"); await flushPromises();
    expect(wrapper.text()).toContain("已失效");
    const remove = wrapper.findAll("button").find(button => button.text() === "准备移除信任")!;
    await remove.trigger("click");
    await wrapper.get('input[placeholder="输入设备 ID"]').setValue("legacy_1");
    await wrapper.findAll("button").find(button => button.text() === "确认操作")!.trigger("click");
    await flushPromises();
    expect(resources.deviceDelete).toHaveBeenCalledWith("trusted-devices/legacy_1");
    wrapper.unmount(); queryClient.clear();
  });
});
