import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import { reactive } from "vue";

import QzoneCapabilitiesPage from "./QzoneCapabilitiesPage.vue";
import { useBotStore } from "@vue-app/stores/bot";
import { resources } from "@/api/resources";
import { api } from "@/api/client";

const currentRoute = reactive<{ params: Record<string, string> }>({
  params: { section: "capabilities" },
});

vi.mock("vue-router", async (importOriginal) => ({
  ...await importOriginal<typeof import("vue-router")>(),
  useRoute: () => currentRoute,
}));

vi.mock("@/api/resources", () => ({
  resources: {
    qzoneCapabilities: vi.fn(),
    qzoneGet: vi.fn(),
    qzonePost: vi.fn(),
  },
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/api/client")>(),
  API_BASE: "/personification/api/v2",
  api: { post: vi.fn() },
}));

describe("QzoneCapabilitiesPage.vue", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    setActivePinia(createPinia());
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
      },
    });
    vi.clearAllMocks();
    currentRoute.params = { section: "capabilities" };
  });

  function createWrapper() {
    return mount(QzoneCapabilitiesPage, {
      global: {
        plugins: [createPinia(), [VueQueryPlugin, { queryClient }]],
        stubs: {
          RouterLink: true,
          DiagnosticPanel: { props: ["diagnostic"], template: '<div class="diagnostic-panel-stub">{{ diagnostic.title }} {{ diagnostic.message }}</div>' },
          EmptyState: { template: '<div class="empty-state-stub"><slot /></div>' },
          PageHeader: {
            props: ["index", "title", "description"],
            template: '<header class="page-header-stub"><h1>{{ title }}</h1><p>{{ description }}</p></header>',
          },
          Panel: {
            props: ["eyebrow", "title"],
            template: '<section class="panel-stub"><h2>{{ title }}</h2><slot /></section>',
          },
          QueryBoundary: {
            props: ["pending", "error"],
            template: '<div class="query-boundary-stub"><slot /></div>',
          },
          StateBadge: {
            props: ["tone"],
            template: '<span class="state-badge-stub"><slot /></span>',
          },
        },
      },
    });
  }

  it("renders capability matrix with structured states and unknown distinct from available", async () => {
    const botStore = useBotStore();
    botStore.setBotId("10001");

    vi.mocked(resources.qzoneCapabilities).mockResolvedValueOnce({
      items: [
        {
          action: "publish",
          state: "available",
          interface: "qzone.publish",
          http_status: 200,
          business_code: 0,
          auth_state: "authenticated",
          detail_code: "ok",
        },
        {
          action: "top_level_comment",
          state: "unknown",
          interface: "qzone.comment",
          http_status: null,
          business_code: null,
          auth_state: "unknown",
          detail_code: "unverified",
        },
      ],
    });

    const wrapper = createWrapper();
    await flushPromises();

    expect(resources.qzoneCapabilities).toHaveBeenCalled();
    const text = wrapper.text();
    expect(text).toContain("发布");
    expect(text).toContain("顶级评论");
    expect(text).toContain("可用");
    expect(text).toContain("待确认");
  });

  it("renders status in auth section and displays read-only status and quota correctly", async () => {
    currentRoute.params = { section: "auth" };
    const botStore = useBotStore();
    botStore.setBotId("10002");

    vi.mocked(resources.qzoneGet).mockImplementation(async (path: string) => {
      if (path === "status") {
        return {
          enabled: true,
          cookie_configured: true,
          credential_source: "onebot",
          read_only: true,
          auth: {
            state: "valid",
            credential_configured: true,
            credential_source: "onebot",
            credential_identity_verification: "verified",
          },
          auth_by_bot: {
            "10002": {
              credential_configured: true,
              credential_source: "onebot",
              credential_identity_verification: "verified",
            },
          },
          capabilities_by_bot: {
            "10002": {
              "qzone.cookie_export": { state: "available" },
              "qzone.web_read": { state: "available" },
              "qzone.web_write": { state: "unknown" },
            },
          },
          quota: { used: 12, limit: 100 },
          reconciliation: { state: "clear", blocking: false, operations: [] },
        };
      }
      return {};
    });

    const wrapper = createWrapper();
    await flushPromises();

    expect(resources.qzoneGet).toHaveBeenCalledWith("status", { bot_id: "10002" }, expect.anything());
    const text = wrapper.text();
    expect(text).toContain("当前登录状态");
    expect(text).toContain("发布额度与状态");
    expect(text).toContain("已配置");
    expect(text).toContain("各 Bot 的登录与功能状态");
    expect(text).toContain("安装时已验证");
    expect(text).toContain("QQ 协议端");
  });

  it("runs a read-only check for the selected Bot when requested", async () => {
    currentRoute.params = { section: "feeds" };
    const botStore = useBotStore();
    botStore.setBotId("10003");

    vi.mocked(resources.qzoneGet).mockResolvedValue({
      enabled: true,
      cookie_configured: false,
      auth: {},
      quota: {},
      reconciliation: { operations: [] },
    });
    vi.mocked(api.post).mockResolvedValue({
      ok: true,
      code: "qzone_read_only_diagnostics_succeeded",
      suggestion: "无需进一步操作。",
      diagnostic: {
        ok: true,
        code: "qzone_read_only_diagnostics_succeeded",
        phase: "read_only_diagnostics",
        title: "QZone 只读诊断完成",
        message: "safe",
        steps: [],
      },
      stages: [
        { key: "identity_match", status: "ok", code: "qzone_read_only_identity_matched", elapsed_ms: 3 },
      ],
    });

    const wrapper = createWrapper();
    await flushPromises();
    expect(api.post).not.toHaveBeenCalled();
    const runButton = wrapper.findAll("button").find((button) => button.text() === "开始读取检查");
    expect(runButton?.attributes("disabled")).toBeUndefined();

    await runButton?.trigger("click");
    await flushPromises();

    expect(api.post).toHaveBeenCalledWith("/qzone/diagnostics/read-only", {
      bot_id: "10003",
      target_user_id: "",
      confirm_external_read: true,
    });
    expect(wrapper.text()).toContain("读取检查结果");
    expect(wrapper.text()).toContain("qzone_read_only_identity_matched");
  });

  it.each([
    [{ diagnostic: { ok: true, title: "已发布" } }, "已发布"],
    [{
      ok: false,
      code: "semantic_reviewer_model_missing",
      phase: "semantic_review",
      title: "语义审阅模型不可用",
      message: "审阅模型接口返回 404，尚未发布。",
    }, "语义审阅模型不可用 审阅模型接口返回 404，尚未发布。"],
  ])("asks once and displays the publish result returned by the API", async (result, expected) => {
    currentRoute.params = { section: "operations" };
    useBotStore().setBotId("10004");
    vi.mocked(resources.qzonePost).mockResolvedValue(result);
    const wrapper = createWrapper();
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "生成并发布")?.trigger("click");
    expect(resources.qzonePost).not.toHaveBeenCalled();
    expect(wrapper.get('[role="dialog"]').text()).toContain("10004");
    await wrapper.get(".dialog-footer .button-danger").trigger("click");
    await flushPromises();
    expect(resources.qzonePost).toHaveBeenCalledOnce();
    expect(resources.qzonePost).toHaveBeenCalledWith("post-now", {
      bot_id: "10004", operation_id: expect.stringMatching(/^qzone-[a-f0-9]{32}$/),
    });
    expect(wrapper.get(".diagnostic-panel-stub").text()).toBe(expected);
  });
});
