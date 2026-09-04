import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { createMemoryHistory, createRouter } from "vue-router";

import OperationsBusinessPages from "../pages/OperationsBusinessPages.vue";
import { resources } from "@/api/resources";

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

  it("renders Data Transfer page and handles stage forms", async () => {
    const { pinia, queryClient, router } = createTestSetup("/operations/data-transfer/export", "data-transfer");
    await router.isReady();

    const wrapper = mount(OperationsBusinessPages, {
      props: { mode: "data-transfer" },
      global: {
        plugins: [pinia, [VueQueryPlugin, { queryClient }], router],
      },
    });

    expect(wrapper.text()).toContain("数据迁移");
    expect(wrapper.find("button.button-primary").text()).toBe("创建导出");
  });
});
