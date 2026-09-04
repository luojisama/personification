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

vi.mock("vue-router", () => ({
  useRoute: () => currentRoute,
}));

vi.mock("@/api/resources", () => ({
  resources: {
    qzoneCapabilities: vi.fn(),
    qzoneGet: vi.fn(),
    qzonePost: vi.fn(),
  },
}));

vi.mock("@/api/client", () => ({
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
          DiagnosticPanel: { template: '<div class="diagnostic-panel-stub" />' },
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
    expect(text).toContain("available");
    expect(text).toContain("unknown");
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
    expect(text).toContain("登录与运行态");
    expect(text).toContain("额度与结果核对");
    expect(text).toContain("已配置（不回传原值）");
    expect(text).toContain("按 Bot 隔离的凭据与能力");
    expect(text).toContain("安装时已验证");
    expect(text).toContain("onebot");
  });

  it("requires explicit confirmation before invoking the exact v2 read-only diagnostic", async () => {
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
    const runButton = wrapper.findAll("button").find((button) => button.text().includes("运行 7 阶段只读诊断"));
    expect(runButton?.attributes("disabled")).toBeDefined();

    await wrapper.find('input[type="checkbox"]').setValue(true);
    await runButton?.trigger("click");
    await flushPromises();

    expect(api.post).toHaveBeenCalledWith("/qzone/diagnostics/read-only", {
      bot_id: "10003",
      target_user_id: "",
      confirm_external_read: true,
    });
    expect(wrapper.text()).toContain("最近只读诊断阶段");
    expect(wrapper.text()).toContain("qzone_read_only_identity_matched");
  });

  it("keeps unsupported write operations disabled in operations section", async () => {
    currentRoute.params = { section: "operations" };
    const wrapper = createWrapper();
    await flushPromises();

    const buttons = wrapper.findAll("button[disabled]");
    const disabledLabels = buttons.map((b) => b.text());
    expect(disabledLabels).toContain("点赞");
    expect(disabledLabels).toContain("转发");
  });
});
