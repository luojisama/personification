import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { MultimodalRouteSnapshot } from "@/api/types";
import SystemDiagnosticsPage from "./SystemDiagnosticsPage.vue";

vi.mock("@/api/resources", () => ({
  resources: {
    multimodalRoutes: vi.fn(),
    qzoneCapabilities: vi.fn(),
    runtimeSettings: vi.fn(),
  },
}));

const mockMultimodal: MultimodalRouteSnapshot = {
  audio: {
    enabled: true,
    primary_native: false,
    route_available: true,
    asr_provider: "faster-whisper",
    asr_model: "large-v3",
    fallback_order: ["local_asr", "gemini_audio"],
  },
  video: {
    enabled: true,
    route_mode: "storyboard",
    primary_native: false,
    gemini_web_enabled: false,
    external_fallback_enabled: true,
    storyboard_fallback_enabled: true,
    fallback_order: ["storyboard_v2"],
  },
  diagnostic_code: "multimodal_local_ready",
  production_verified: false,
  dependencies: {
    ffmpeg: { available: true, version: "6.1.1", diagnostic_code: "ffmpeg_ok" },
    ffprobe: { available: true, version: "6.1.1", diagnostic_code: "ffprobe_ok" },
  },
};

describe("SystemDiagnosticsPage", () => {
  beforeEach(() => {
    vi.mocked(resources.multimodalRoutes).mockResolvedValue(mockMultimodal);
    vi.mocked(resources.qzoneCapabilities).mockResolvedValue({
      login_state: "logged_in",
      publish_feed: { state: "supported", detail_code: "qzone_feed_ready" },
      like_feed: { state: "unknown", detail_code: "unverified_session" },
    });
    vi.mocked(resources.runtimeSettings).mockResolvedValue({
      participation_v2_mode: "shadow",
      tool_disclosure_mode: "on",
      emotion_v2_mode: "on",
    });
  });

  async function renderPage() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/operations/systems/:section", component: SystemDiagnosticsPage }],
    });
    await router.push("/operations/systems/multimodal");
    await router.isReady();

    const wrapper = mount(SystemDiagnosticsPage, {
      global: { plugins: [[VueQueryPlugin, { queryClient }], router] },
    });
    return { wrapper, queryClient };
  }

  it("正确渲染音频视频多模态路由、QZone 结构化状态与功能开关", async () => {
    const { wrapper, queryClient } = await renderPage();
    await vi.waitFor(() => expect(wrapper.text()).toContain("faster-whisper"));
    expect(wrapper.text()).toContain("ffmpeg / ffprobe 已就绪");
    expect(wrapper.text()).toContain("publish_feed");
    expect(wrapper.text()).toContain("参与概率 v2");
    wrapper.unmount();
    queryClient.clear();
  });
});
