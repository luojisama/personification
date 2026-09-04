import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { Page, RouteCapabilityItem } from "@/api/types";
import RouteCapabilitiesPage from "./RouteCapabilitiesPage.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    routes: vi.fn(),
    queueRouteProbe: vi.fn(),
    uploadRouteMediaProbe: vi.fn(),
  },
}));

const mockRouteItem: RouteCapabilityItem = {
  route_fingerprint: "rf_test_1234567890",
  provider: "openai",
  api_type: "chat_completions",
  model: "gpt-4o",
  media_protocol: "native_base64",
  probe_status: "idle",
  capabilities: {
    image_input: { state: "supported", verification_state: "verified", source: "runtime_success", checked_at: 1700000000000, expires_at: null, detail_code: "ok" },
    audio_input: { state: "unsupported", verification_state: "verified", source: "provider_catalog", checked_at: 1700000000000, expires_at: null, detail_code: "unsupported_by_schema" },
    video_input: { state: "unknown", verification_state: "inconclusive", source: "heuristic", checked_at: null, expires_at: null, detail_code: "probe_video_inconclusive" },
    reasoning: { state: "supported", verification_state: "not_run", source: "model_catalog", checked_at: 1700000000000, expires_at: null, detail_code: "ok" },
    function_call: { state: "supported", verification_state: "verified", source: "runtime_success", checked_at: 1700000000000, expires_at: null, detail_code: "ok" },
    native_web_search: { state: "unknown", verification_state: "probe_unavailable", source: "heuristic", checked_at: null, expires_at: null, detail_code: "native_search_probe_unavailable_confirmation_required" },
    external_network_access: { state: "unknown", verification_state: "stale", source: "heuristic", checked_at: null, expires_at: null, detail_code: "policy_unspecified" },
  },
  probe_catalog: {
    image_input: { probe_id: "vision", available: true, risk: "external_read", confirmation_required: true, reason_code: "vision_probe_available" },
    audio_input: { probe_id: "audio_upload", available: true, risk: "external_read", confirmation_required: true, reason_code: "audio_probe_upload_available", input_kind: "media_upload", accepted_mime_types: ["audio/wav"], max_upload_bytes: 12 * 1024 * 1024 },
    video_input: { probe_id: "video_upload", available: true, risk: "external_read", confirmation_required: true, reason_code: "video_probe_upload_available", input_kind: "media_upload", accepted_mime_types: ["video/mp4"], max_upload_bytes: 32 * 1024 * 1024 },
    reasoning: { probe_id: "reasoning_minimal", available: true, risk: "external_read", confirmation_required: true, reason_code: "reasoning_minimal_probe_available" },
    function_call: { probe_id: "function_call_noop", available: true, risk: "external_read", confirmation_required: true, reason_code: "function_call_noop_probe_available" },
    native_web_search: { probe_id: "native_search_readonly", available: true, risk: "external_read", confirmation_required: true, reason_code: "native_search_readonly_probe_available" },
    external_network_access: { probe_id: "none", available: false, risk: "external_read", confirmation_required: true, reason_code: "external_network_probe_unavailable" },
  },
  probe_statuses: {
    image_input: "idle",
    audio_input: "idle",
    video_input: "idle",
    reasoning: "idle",
    function_call: "idle",
    native_web_search: "idle",
    external_network_access: "idle",
  },
};

const mockPageData: Page<RouteCapabilityItem> = {
  items: [mockRouteItem],
  page: 1,
  page_size: 20,
  total: 1,
  total_pages: 1,
};

describe("RouteCapabilitiesPage", () => {
  beforeEach(() => {
    vi.mocked(resources.routes).mockResolvedValue(mockPageData);
    vi.mocked(resources.queueRouteProbe).mockResolvedValue({
      ok: true,
      code: "probe_queued",
      phase: "dispatch",
      title: "探针已入队",
      message: "测试探针已排队",
      retryable: false,
      partial: false,
      outcome_unknown: false,
      warnings: [],
      steps: [],
    });
    vi.mocked(resources.uploadRouteMediaProbe).mockResolvedValue({
      ok: true,
      code: "probe_queued",
      phase: "dispatch",
      title: "媒体探针已入队",
      message: "受限样例已排队",
      retryable: false,
      partial: false,
      outcome_unknown: false,
      warnings: [],
      steps: [],
    });
  });

  async function renderPage() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/routes/:section", component: RouteCapabilitiesPage }],
    });
    await router.push("/runtime/routes/capabilities");
    await router.isReady();

    const wrapper = mount(RouteCapabilitiesPage, {
      global: { plugins: [[VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, queryClient };
  }

  it("分别呈现能力状态、验证状态和探针可用性，且不把不确定结果渲染成成功", async () => {
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("gpt-4o"));
    expect(wrapper.text()).toContain("openai / chat_completions");
    expect(wrapper.text()).toContain("已验证支持 2");
    expect(wrapper.text()).toContain("待核实 4");
    expect(wrapper.text()).toContain("已验证不支持 1");
    expect(wrapper.text()).toContain("结果不确定");
    expect(wrapper.text()).toContain("探针不可用");
    expect(wrapper.text()).toContain("Provider 外部读取");
    const videoCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("视频: 未知"));
    expect(videoCell).toBeDefined();
    expect(videoCell?.find(".state-ok").exists()).toBe(false);
    wrapper.unmount();
    queryClient.clear();
  });

  it("只为可用探针请求明确确认，并按能力提交探针请求", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("函数: 支持"));

    const functionCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("函数: 支持"));
    expect(functionCell).toBeDefined();
    await functionCell?.find("button").trigger("click");

    expect(window.confirm).toHaveBeenCalled();
    expect(resources.queueRouteProbe).toHaveBeenCalledWith("rf_test_1234567890", "function_call", true);
    await vi.waitFor(() => expect(functionCell?.find("button").attributes("disabled")).toBeUndefined());

    const nativeSearchCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("原生搜索: 未知"));
    expect(nativeSearchCell?.find("button").attributes("disabled")).toBeUndefined();
    await nativeSearchCell?.find("button").trigger("click");
    expect(resources.queueRouteProbe).toHaveBeenCalledWith("rf_test_1234567890", "native_web_search", true);
    await vi.waitFor(() => expect(nativeSearchCell?.find("button").attributes("disabled")).toBeUndefined());

    const audioCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("音频: 不支持"));
    expect(audioCell).toBeDefined();
    expect(audioCell?.text()).toContain("管理员受限音频样例");
    expect(audioCell?.find("button").attributes("disabled")).toBeDefined();
    const sample = new File(["RIFF"], "sample.wav", { type: "audio/wav" });
    const input = audioCell?.find('[data-testid="route-media-probe-input"]');
    expect(input?.exists()).toBe(true);
    Object.defineProperty((input?.element as HTMLInputElement), "files", {
      value: { 0: sample, length: 1, item: (index: number) => (index === 0 ? sample : null) },
      configurable: true,
    });
    await input?.trigger("change");
    expect(audioCell?.text()).toContain("已选择受限样例");
    expect(audioCell?.find("button").attributes("disabled")).toBeUndefined();
    await audioCell?.find("button").trigger("click");
    expect(resources.uploadRouteMediaProbe).toHaveBeenCalledWith("rf_test_1234567890", "audio_input", sample);
    wrapper.unmount();
    queryClient.clear();
  });
});
