import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import ModelTestsPage from "./ModelTestsPage.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    personaPromptPreview: vi.fn(),
    modelChat: vi.fn(),
    videoRouteProbe: vi.fn(),
    videoTurnTest: vi.fn(),
  },
}));

describe("ModelTestsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function renderPage() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/runtime/model-tests/:section", component: ModelTestsPage }],
    });
    await router.push("/runtime/model-tests/overview");
    await router.isReady();

    const wrapper = mount(ModelTestsPage, {
      global: { plugins: [[VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, queryClient };
  }

  it("初始渲染不自动加载人设 Prompt，点击加载后拉取并展示", async () => {
    vi.mocked(resources.personaPromptPreview).mockResolvedValue({
      source: "custom_prompt.yaml",
      exists: true,
      is_file: true,
      size: 1024,
      content: "你是小助手，性格温和。",
    });

    const { wrapper, queryClient } = await renderPage();
    expect(resources.personaPromptPreview).not.toHaveBeenCalled();

    const loadBtn = wrapper.findAll("button").find((b) => b.text().includes("加载当前 Prompt"));
    expect(loadBtn).toBeDefined();
    await loadBtn!.trigger("click");

    await vi.waitFor(() => {
      expect(wrapper.text()).toContain("custom_prompt.yaml");
      expect(wrapper.text()).toContain("你是小助手，性格温和。");
    });

    wrapper.unmount();
    queryClient.clear();
  });

  it("模型单路由/全路由测试在确认取消时不调用 API，确认后才发起并渲染结果", async () => {
    vi.mocked(resources.modelChat).mockResolvedValue({
      ok: true,
      code: "chat_test_ok",
      duration_ms: 320,
      outbound: "captured_not_sent",
      trace_id: "trace_chat_001",
      summary: { succeeded: 1, failed: 0 },
      results: [
        {
          name: "openai-main",
          model: "gpt-4o",
          ok: true,
          duration_ms: 320,
          content: "当前模型连接正常。",
        },
      ],
    });

    const confirmSpy = vi.spyOn(window, "confirm");
    confirmSpy.mockReturnValueOnce(false);

    const { wrapper, queryClient } = await renderPage();
    const singleBtn = wrapper.findAll("button").find((b) => b.text().includes("测试当前路由"));
    await singleBtn!.trigger("click");

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("产生额度消耗"));
    expect(resources.modelChat).not.toHaveBeenCalled();

    confirmSpy.mockReturnValueOnce(true);
    await singleBtn!.trigger("click");

    await vi.waitFor(() => {
      expect(resources.modelChat).toHaveBeenCalledWith("single", expect.any(String));
      expect(wrapper.text()).toContain("openai-main");
      expect(wrapper.text()).toContain("当前模型连接正常。");
      expect(wrapper.text()).toContain("trace_chat_001");
    });

    confirmSpy.mockRestore();
    wrapper.unmount();
    queryClient.clear();
  });

  it("视频路由探针与完整回合要求文件和明确确认", async () => {
    vi.mocked(resources.videoRouteProbe).mockResolvedValue({
      ok: true,
      code: "video_probe_ok",
      duration_ms: 1200,
      categories: [
        {
          name: "路由适配",
          checks: [{ key: "media_decode", label: "视频解码测试", state: "ok", detail: "帧抽取通过" }],
        },
      ],
    });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { wrapper, queryClient } = await renderPage();

    const probeBtn = wrapper.findAll("button").find((b) => b.text().includes("确认并运行路由探针"));
    expect(probeBtn!.attributes("disabled")).toBeDefined();

    const file = new File(["dummy"], "sample.mp4", { type: "video/mp4" });
    const fileInput = wrapper.find("input[type='file']");
    Object.defineProperty(fileInput.element, "files", { value: [file], writable: false });
    await fileInput.trigger("change");

    expect(probeBtn!.attributes("disabled")).toBeUndefined();
    await probeBtn!.trigger("click");

    await vi.waitFor(() => {
      expect(resources.videoRouteProbe).toHaveBeenCalledWith(file);
      expect(wrapper.text()).toContain("视频解码测试");
    });

    confirmSpy.mockRestore();
    wrapper.unmount();
    queryClient.clear();
  });
});
