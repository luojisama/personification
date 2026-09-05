import { mount } from "@vue/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import FunctionalTestsPage from "./FunctionalTestsPage.vue";
import { resources } from "@/api/resources";
import type { HealthCatalog, FunctionalTestRun } from "@/api/types";

vi.mock("@/api/resources", () => ({
  resources: {
    health: vi.fn(),
    prepareTestRun: vi.fn(),
    confirmTestRun: vi.fn(),
    testRun: vi.fn(),
    prepareSafeTestBatch: vi.fn(),
    confirmSafeTestBatch: vi.fn(),
    testBatch: vi.fn(),
    cancelTestBatch: vi.fn(),
  },
}));

const mockCatalog: HealthCatalog = {
  tests: [
    { id: "test_local", label: "本地配置检查", category: "runtime", group: "核心运行", risk: "local_read", execution_kind: "local_readonly" },
    { id: "test_ext_read", label: "LLM 连通性测试", category: "models", group: "模型与媒体", risk: "external_read", execution_kind: "provider_probe" },
    { id: "test_storage", label: "记忆存储检查", category: "storage", group: "存储与记忆", risk: "local_read", execution_kind: "local_readonly" },
    { id: "test_ext_write", label: "群主动消息发送", category: "messaging", group: "QQ 与群聊", risk: "external_write", execution_kind: "qq_canary" },
    { id: "test_qzone", label: "QZone 单目标 canary", category: "qzone", group: "QZone", risk: "external_write", execution_kind: "qzone_canary" },
    { id: "test_background", label: "后台权限检查", category: "permissions", group: "后台任务与权限", risk: "local_read", execution_kind: "local_readonly" },
  ],
  cached: { ok: true },
  diagnostic_code: "health_catalog_ready",
};

describe("FunctionalTestsPage.vue", () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    vi.mocked(resources.health).mockResolvedValue(mockCatalog);
  });

  function createWrapper() {
    return mount(FunctionalTestsPage, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient }]],
      },
    });
  }

  it("按六个运行边界分组，并呈现风险与执行方式", async () => {
    const wrapper = createWrapper();
    await wrapper.vm.$nextTick();
    // 等待 Query 解析
    await new Promise((r) => setTimeout(r, 10));
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain("本地配置检查");
    expect(wrapper.text()).toContain("LLM 连通性测试");
    expect(wrapper.text()).toContain("群主动消息发送");
    expect(wrapper.text()).toContain("本地只读");
    expect(wrapper.text()).toContain("外部读取");
    expect(wrapper.text()).toContain("外部写入");
    expect(wrapper.text()).toContain("核心运行");
    expect(wrapper.text()).toContain("模型与媒体");
    expect(wrapper.text()).toContain("存储与记忆");
    expect(wrapper.text()).toContain("QQ 与群聊");
    expect(wrapper.text()).toContain("QZone");
    expect(wrapper.text()).toContain("后台任务与权限");
    expect(wrapper.text()).toContain("Provider 外部读取探针");
    expect(wrapper.text()).toContain("真实 QQ canary（专用入口）");
  });

  it("external operations do not trigger API call if confirmation is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = createWrapper();
    await new Promise((r) => setTimeout(r, 10));
    await wrapper.vm.$nextTick();

    const card = wrapper.findAll(".health-test-card").find((item) => item.text().includes("LLM 连通性测试"));
    expect(card).toBeDefined();
    await card?.find("button.button-secondary").trigger("click");

    expect(window.confirm).toHaveBeenCalled();
    expect(resources.prepareTestRun).not.toHaveBeenCalled();
  });

  it("安全全检只确认一次并展示被排除的外部写项目", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const batch = {
      id: "batch-1",
      profile: "safe_full" as const,
      state: "awaiting_confirmation" as const,
      created_at: null,
      confirmed_at: null,
      started_at: null,
      finished_at: null,
      expires_at: null,
      cancellation_requested: false,
      confirmation: {
        local_read_items: 3,
        external_read_items: 2,
        external_write_excluded: 2,
        active_media_routes: 1,
        cost_notice: "可能产生额度消耗",
      },
      excluded: [{ test_id: "test_ext_write", label: "群主动消息发送", reason_code: "safe_full_external_write_excluded" }],
      counts: { pending: 5, skipped: 2 },
      items: [],
      diagnostic_code: "safe_full_confirmation_required",
    };
    vi.mocked(resources.prepareSafeTestBatch).mockResolvedValue(batch);
    vi.mocked(resources.confirmSafeTestBatch).mockResolvedValue({ ...batch, state: "running", diagnostic_code: "safe_full_running" });
    const wrapper = createWrapper();
    await vi.waitFor(() => expect(wrapper.text()).toContain("一键安全全检"));
    await wrapper.findAll("button").find((button) => button.text() === "一键安全全检")!.trigger("click");
    await vi.waitFor(() => expect(resources.confirmSafeTestBatch).toHaveBeenCalledWith("batch-1"));
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("群主动消息发送");
    expect(wrapper.text()).toContain("已跳过");
  });

  it("external operations execute full confirmation flow when accepted", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const preparedRun: FunctionalTestRun = {
      id: "run-1",
      test_id: "test_ext_write",
      label: "群主动消息发送",
      group: "QQ 与群聊",
      risk: "external_write",
      execution_kind: "qq_canary",
      state: "awaiting_confirmation",
      target_summary: "123456",
      route_fingerprint: null,
      trace_id: "trace-99",
      diagnostic_code: "ok",
      created_at: "2025-01-01T00:00:00Z",
      started_at: null,
      finished_at: null,
      duration_ms: null,
      steps: [
        { key: "qq_canary", label: "真实 QQ canary", status: "pending", message: "等待管理员确认" },
        { key: "delivery", label: "QQ 交付", status: "skipped", message: "本页不会发送 QQ 消息" },
      ],
      diagnostic: {
        ok: true,
        code: "test_confirmation_required",
        phase: "confirmation",
        title: "体检等待管理员确认",
        message: "本页不会发送 QQ 消息。",
        retryable: false,
        partial: false,
        outcome_unknown: false,
        warnings: [],
        steps: [],
      },
      result_summary: {},
      delivery_status: "not_started",
    };
    const confirmedRun: FunctionalTestRun = {
      ...preparedRun,
      state: "unknown",
      started_at: "2025-01-01T00:00:00Z",
      finished_at: "2025-01-01T00:00:01Z",
      duration_ms: 1000,
      diagnostic_code: "external_write_dedicated_canary_required",
      diagnostic: {
        ...preparedRun.diagnostic,
        ok: false,
        code: "external_write_dedicated_canary_required",
        phase: "delivery_canary",
        title: "真实外部写 canary 需要专用入口",
        message: "本体检页面不会发送 QQ。",
      },
      steps: [
        { key: "qq_canary", label: "真实 QQ canary", status: "skipped", message: "本页没有执行 canary" },
        { key: "delivery", label: "QQ 交付", status: "skipped", message: "需要专用入口" },
      ],
      result_summary: { message: "请在专用页面完成单目标 canary。" },
      delivery_status: "dedicated_canary_required",
    };
    vi.mocked(resources.prepareTestRun).mockResolvedValue(preparedRun);
    vi.mocked(resources.confirmTestRun).mockResolvedValue(confirmedRun);

    const wrapper = createWrapper();
    await new Promise((r) => setTimeout(r, 10));
    await wrapper.vm.$nextTick();

    const card = wrapper.findAll(".health-test-card").find((item) => item.text().includes("群主动消息发送"));
    expect(card).toBeDefined();
    await card?.find("button.button-secondary").trigger("click");

    expect(window.confirm).toHaveBeenCalled();
    expect(resources.prepareTestRun).toHaveBeenCalledWith("test_ext_write", "");
    await new Promise((r) => setTimeout(r, 10));
    expect(resources.confirmTestRun).toHaveBeenCalledWith("run-1", "");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("结果未知");
    expect(wrapper.text()).toContain("Trace");
    expect(wrapper.text()).toContain("需要专用 canary");
    const result = card?.find(".test-run-result");
    expect(result?.find(".state-ok").exists()).toBe(false);
  });
});
