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
  },
}));

const mockCatalog: HealthCatalog = {
  tests: [
    { id: "test_local", label: "本地配置检查", category: "runtime", risk: "local_read" },
    { id: "test_ext_read", label: "LLM 连通性测试", category: "models", risk: "external_read" },
    { id: "test_ext_write", label: "群主动消息发送", category: "messaging", risk: "external_write" },
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

  it("renders catalog items and risk badges", async () => {
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
  });

  it("external operations do not trigger API call if confirmation is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = createWrapper();
    await new Promise((r) => setTimeout(r, 10));
    await wrapper.vm.$nextTick();

    // 点击外部读取体检按钮
    const buttons = wrapper.findAll("button.button-secondary");
    const targetButton = buttons[1];
    expect(targetButton).toBeDefined();
    await targetButton?.trigger("click");

    expect(window.confirm).toHaveBeenCalled();
    expect(resources.prepareTestRun).not.toHaveBeenCalled();
  });

  it("external operations execute full confirmation flow when accepted", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const preparedRun: FunctionalTestRun = {
      id: "run-1",
      test_id: "test_ext_write",
      label: "群主动消息发送",
      risk: "external_write",
      state: "awaiting_confirmation",
      target_summary: "123456",
      route_fingerprint: null,
      trace_id: "trace-99",
      diagnostic_code: "ok",
      created_at: "2025-01-01T00:00:00Z",
      finished_at: "2025-01-01T00:00:01Z",
      duration_ms: 1000,
      result_summary: { target_sent: true, latency: 45 },
    };
    const confirmedRun: FunctionalTestRun = {
      ...preparedRun,
      state: "succeeded",
    };
    vi.mocked(resources.prepareTestRun).mockResolvedValue(preparedRun);
    vi.mocked(resources.confirmTestRun).mockResolvedValue(confirmedRun);

    const wrapper = createWrapper();
    await new Promise((r) => setTimeout(r, 10));
    await wrapper.vm.$nextTick();

    const buttons = wrapper.findAll("button.button-secondary");
    const targetButton = buttons[2];
    expect(targetButton).toBeDefined();
    await targetButton?.trigger("click");

    expect(window.confirm).toHaveBeenCalled();
    expect(resources.prepareTestRun).toHaveBeenCalledWith("test_ext_write", "");
    await new Promise((r) => setTimeout(r, 10));
    expect(resources.confirmTestRun).toHaveBeenCalledWith("run-1", "");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("target_sent");
    expect(wrapper.text()).toContain("45");
  });
});
