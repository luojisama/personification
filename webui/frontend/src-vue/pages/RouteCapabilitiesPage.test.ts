import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { Page, RouteCapabilityItem, RouteProbeOperation } from "@/api/types";
import RouteCapabilitiesPage from "./RouteCapabilitiesPage.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    routes: vi.fn(),
    queueRouteProbe: vi.fn(),
    uploadRouteMediaProbe: vi.fn(),
    routeProbeOperation: vi.fn(),
    routeProbeHistory: vi.fn(),
    cancelRouteProbeOperation: vi.fn(),
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
    audio_input: { probe_id: "audio_builtin", available: true, risk: "external_read", confirmation_required: true, reason_code: "audio_probe_available", input_kind: "media", sample_modes: ["builtin", "upload"], default_sample_id: "audio-ascending-v1", builtin_sample: { description: "三段短音调" }, accepted_mime_types: ["audio/wav"], max_upload_bytes: 12 * 1024 * 1024 },
    video_input: { probe_id: "video_builtin", available: true, risk: "external_read", confirmation_required: true, reason_code: "video_probe_available", input_kind: "media", sample_modes: ["builtin", "upload"], default_sample_id: "video-rgb-v1", builtin_sample: { description: "三段生成画面" }, accepted_mime_types: ["video/mp4"], max_upload_bytes: 32 * 1024 * 1024 },
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
    vi.clearAllMocks();
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
    vi.mocked(resources.routeProbeHistory).mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0, total_pages: 1 });
  });

  async function renderPage(section = "capabilities", queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/routes/:section", component: RouteCapabilitiesPage }],
    });
    await router.push(`/runtime/routes/${section}`);
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
    const { wrapper, queryClient } = await renderPage("probes");
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
    expect(audioCell?.text()).toContain("内置确定性样例（默认）");
    expect(audioCell?.find("button").attributes("disabled")).toBeUndefined();
    await audioCell?.find("button").trigger("click");
    expect(resources.queueRouteProbe).toHaveBeenCalledWith("rf_test_1234567890", "audio_input", true, "builtin", "audio-ascending-v1");

    await vi.waitFor(() => expect(wrapper.text()).toContain("内置确定性样例（默认）"));
    const refreshedAudioCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("音频: 不支持"));
    const uploadRadio = refreshedAudioCell?.findAll("input[type='radio']")[1];
    expect(uploadRadio?.exists()).toBe(true);
    await uploadRadio!.setValue(true);
    expect(refreshedAudioCell?.text()).toContain("管理员受限音频样例");
    expect(refreshedAudioCell?.find("button").attributes("disabled")).toBeDefined();
    const sample = new File(["RIFF"], "sample.wav", { type: "audio/wav" });
    const input = refreshedAudioCell?.find('[data-testid="route-media-probe-input"]');
    expect(input?.exists()).toBe(true);
    Object.defineProperty((input?.element as HTMLInputElement), "files", {
      value: { 0: sample, length: 1, item: (index: number) => (index === 0 ? sample : null) },
      configurable: true,
    });
    await input?.trigger("change");
    expect(refreshedAudioCell?.text()).toContain("已选择受限样例");
    expect(refreshedAudioCell?.find("button").attributes("disabled")).toBeUndefined();
    await refreshedAudioCell?.find("button").trigger("click");
    expect(resources.uploadRouteMediaProbe).toHaveBeenCalledWith("rf_test_1234567890", "audio_input", sample);
    wrapper.unmount();
    queryClient.clear();
  });

  it("显示持久化任务的真实阶段，并允许取消仍在运行的任务", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(resources.queueRouteProbe).mockResolvedValueOnce({
      ok: true, code: "route_probe_queued", phase: "queued", title: "已排队", message: "等待执行", retryable: false,
      partial: false, outcome_unknown: false, warnings: [], steps: [], operation_id: "rp-visible",
    });
    vi.mocked(resources.routeProbeOperation).mockResolvedValue({
      operation_id: "rp-visible", route_fingerprint: "rf_test_1234567890", capability: "function_call", source: "manual",
      status: "running", detail_code: "probe_running", capability_state: "unknown", verification_state: "not_run",
      transport_verified: false, content_verified: false, queued_at: 1710000000000, started_at: 1710000001000,
      finished_at: null, duration_ms: 0, facts: { last_verified: null, latest_attempt: null },
    });
    vi.mocked(resources.cancelRouteProbeOperation).mockResolvedValue({
      ...(await vi.mocked(resources.routeProbeOperation)("rp-visible")), status: "cancel_requested",
    });
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("函数: 支持"));
    const functionCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("函数: 支持"));
    await functionCell?.find("button").trigger("click");
    await vi.waitFor(() => expect(resources.routeProbeOperation).toHaveBeenCalledWith("rp-visible"));
    await vi.waitFor(() => expect(wrapper.text()).toContain("阶段：执行"));
    await functionCell?.find(".probe-operation-feedback button").trigger("click");
    expect(resources.cancelRouteProbeOperation).toHaveBeenCalledWith("rp-visible");
    wrapper.unmount();
    queryClient.clear();
  });

  it("任务从运行中到成功时只刷新一次快照和历史，并直接展示两项验证结果", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(resources.queueRouteProbe).mockResolvedValueOnce({
      ok: true, code: "route_probe_queued", phase: "queued", title: "已排队", message: "等待执行", retryable: false,
      partial: false, outcome_unknown: false, warnings: [], steps: [], operation_id: "rp-succeeded",
    });
    const runningOperation: RouteProbeOperation = {
      operation_id: "rp-succeeded", route_fingerprint: "rf_test_1234567890", capability: "audio_input" as const, source: "manual",
      status: "running", detail_code: "probe_running", capability_state: "unknown" as const, verification_state: "not_run" as const,
      transport_verified: false, content_verified: false, queued_at: 1710000000000, started_at: 1710000001000,
      finished_at: null, duration_ms: 0, facts: { last_verified: null, latest_attempt: null },
    };
    const succeededOperation: RouteProbeOperation = {
      operation_id: "rp-succeeded", route_fingerprint: "rf_test_1234567890", capability: "audio_input", source: "manual",
      status: "succeeded", detail_code: "audio_input_builtin_content_verified", capability_state: "supported", verification_state: "verified",
      transport_verified: true, content_verified: true, queued_at: 1710000000000, started_at: 1710000001000,
      finished_at: 1710000002000, duration_ms: 1000, facts: { last_verified: null, latest_attempt: null },
    };
    vi.mocked(resources.routeProbeOperation).mockResolvedValueOnce(runningOperation).mockResolvedValue(succeededOperation);
    vi.mocked(resources.routes)
      .mockResolvedValueOnce(mockPageData)
      .mockResolvedValue({
        ...mockPageData,
        items: [{ ...mockRouteItem, probe_facts: { audio_input: { last_verified: succeededOperation, latest_attempt: succeededOperation } } }],
      });
    const { wrapper, queryClient } = await renderPage("probes");
    await vi.waitFor(() => expect(wrapper.text()).toContain("音频: 不支持"));
    const audioCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("音频: 不支持"));
    await audioCell?.find("button").trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("阶段：执行"));
    await vi.waitFor(() => expect(wrapper.text()).toContain("传输/解码已验证"));
    expect(wrapper.text()).toContain("内容理解已验证");
    await vi.waitFor(() => expect(wrapper.text()).toContain("最近有效验证03/10 00:00:02"));
    await vi.waitFor(() => expect(resources.routes).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(resources.routeProbeHistory).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resources.routes).toHaveBeenCalledTimes(2);
    expect(resources.routeProbeHistory).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    queryClient.clear();
  });

  it("重新打开后从终态 latest_attempt 恢复任务，且显示失败诊断", async () => {
    const lastVerifiedOperation: RouteProbeOperation = {
      operation_id: "rp-verified", route_fingerprint: "rf_test_1234567890", capability: "video_input", source: "manual",
      status: "succeeded", detail_code: "video_input_builtin_content_verified", capability_state: "supported", verification_state: "verified",
      transport_verified: true, content_verified: true, queued_at: 1700000000000, started_at: 1700000001000,
      finished_at: 1700000002000, duration_ms: 1000, facts: { last_verified: null, latest_attempt: null },
    };
    const failedOperation = {
      operation_id: "rp-failed", route_fingerprint: "rf_test_1234567890", capability: "video_input" as const, source: "manual",
      status: "failed", detail_code: "video_input_builtin_content_mismatch", capability_state: "unknown" as const, verification_state: "inconclusive" as const,
      transport_verified: true, content_verified: false, queued_at: 1710000000000, started_at: 1710000001000,
      finished_at: 1710000002000, duration_ms: 1000, facts: { last_verified: null, latest_attempt: null },
    };
    vi.mocked(resources.routes).mockResolvedValue({
      ...mockPageData,
      items: [{ ...mockRouteItem, probe_facts: { video_input: { last_verified: lastVerifiedOperation, latest_attempt: failedOperation } } }],
    });
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("内置视频样例的内容核验不匹配"));
    expect(wrapper.text()).toContain("传输/解码已验证");
    expect(wrapper.text()).toContain("内容理解未验证");
    expect(wrapper.text()).toContain("最近有效验证11/15 06:13:22");
    await vi.waitFor(() => expect(resources.routes).toHaveBeenCalledTimes(2));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(resources.routes).toHaveBeenCalledTimes(2);
    wrapper.unmount();
    queryClient.clear();
  });

  it("挂载时立即恢复缓存快照中的终态任务", async () => {
    const cachedOperation: RouteProbeOperation = {
      operation_id: "rp-cached", route_fingerprint: "rf_test_1234567890", capability: "video_input", source: "manual",
      status: "failed", detail_code: "gemini_response_no_text", capability_state: "unknown", verification_state: "inconclusive",
      transport_verified: true, content_verified: false, queued_at: 1710000000000, started_at: 1710000001000,
      finished_at: 1710000002000, duration_ms: 1000, facts: { last_verified: null, latest_attempt: null },
    };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["route-capabilities", 1, ""], {
      ...mockPageData,
      items: [{ ...mockRouteItem, probe_facts: { video_input: { latest_attempt: cachedOperation } } }],
    });
    const { wrapper } = await renderPage("capabilities", queryClient);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Gemini 候选未包含文本内容"));
    expect(wrapper.text()).toContain("内容理解未验证");
    wrapper.unmount();
    queryClient.clear();
  });

  it("本页新提交任务不被迟到的旧快照覆盖", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(resources.queueRouteProbe).mockResolvedValueOnce({
      ok: true, code: "route_probe_queued", phase: "queued", title: "已排队", message: "等待执行", retryable: false,
      partial: false, outcome_unknown: false, warnings: [], steps: [], operation_id: "rp-new",
    });
    vi.mocked(resources.routeProbeOperation).mockResolvedValue({
      operation_id: "rp-new", route_fingerprint: "rf_test_1234567890", capability: "function_call", source: "manual",
      status: "running", detail_code: "probe_running", capability_state: "unknown", verification_state: "not_run",
      transport_verified: false, content_verified: false, queued_at: 1710000000000, started_at: 1710000001000,
      finished_at: null, duration_ms: 0, facts: { last_verified: null, latest_attempt: null },
    });
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("函数: 支持"));
    const functionCell = wrapper.findAll(".capability-cell").find((cell) => cell.text().includes("函数: 支持"));
    await functionCell?.find("button").trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("运行中"));
    const staleOperation: RouteProbeOperation = {
      operation_id: "rp-old", route_fingerprint: "rf_test_1234567890", capability: "function_call", source: "manual",
      status: "failed", detail_code: "probe_timeout", capability_state: "unknown", verification_state: "inconclusive",
      transport_verified: false, content_verified: false, queued_at: 1700000000000, started_at: 1700000001000,
      finished_at: 1700000002000, duration_ms: 1000, facts: { last_verified: null, latest_attempt: null },
    };
    queryClient.setQueryData(["route-capabilities", 1, ""], {
      ...mockPageData,
      items: [{ ...mockRouteItem, probe_facts: { function_call: { latest_attempt: staleOperation } } }],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(functionCell?.text()).toContain("运行中");
    expect(functionCell?.text()).not.toContain("本次探测超时");
    wrapper.unmount();
    queryClient.clear();
  });

  it("排队失败有可见反馈且解除按钮忙碌状态", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(resources.queueRouteProbe).mockRejectedValueOnce(new Error("network"));
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("函数: 支持"));
    const cell = wrapper.findAll(".capability-cell").find(item => item.text().includes("函数: 支持"))!;
    await cell.find("button").trigger("click");
    await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain("探针未能排队"));
    expect(cell.find("button").attributes("disabled")).toBeUndefined();
    wrapper.unmount(); queryClient.clear();
  });
});
