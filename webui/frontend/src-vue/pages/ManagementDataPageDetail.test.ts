import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";

import ManagementDataPage from "./ManagementDataPage.vue";
import { resources } from "@/api/resources";

vi.mock("@/api/resources", () => ({
  resources: {
    personasFiltered: vi.fn(),
    personaDetail: vi.fn(),
  },
}));

describe("ManagementDataPage.vue persona detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it("renders projected structured fields with value, source, confidence and update time", async () => {
    vi.mocked(resources.personasFiltered).mockResolvedValue({
      items: [],
      page: 1,
      page_size: 20,
      total: 0,
      total_pages: 0,
    });
    vi.mocked(resources.personaDetail).mockResolvedValue({
      core_profile: {
        updated_at: "2026-09-04T10:00:00Z",
        profile_text: "ORIGINAL_PRIVATE_PROFILE_DO_NOT_RENDER",
        qq_profile: { nickname: "小林" },
        structured_fields: [
          {
            key: "interests",
            label: "兴趣偏好",
            value: "音乐、游戏",
            source: "system_observation",
            confidence: 0.86,
            updated_at: "2026-09-04T09:30:00Z",
          },
        ],
      },
      favorability: { score: 0, level: "初见" },
    });

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/persona/personas/:section", component: ManagementDataPage }],
    });
    await router.push({ path: "/persona/personas/detail", query: { user_id: "10001", group_id: "20002" } });
    await router.isReady();

    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = mount(ManagementDataPage, {
      props: { dataset: "personas" },
      global: {
        plugins: [createPinia(), router, [VueQueryPlugin, { queryClient }]],
      },
    });
    await flushPromises();

    const table = wrapper.get('table[aria-label="结构化画像字段"]');
    expect(table.text()).toContain("兴趣偏好");
    expect(table.text()).toContain("音乐、游戏");
    expect(table.text()).toContain("系统观察");
    expect(table.text()).toContain("0.86");
    expect(table.text()).toMatch(/09\/04\s+17:30:00/);
    expect(wrapper.text()).not.toContain("ORIGINAL_PRIVATE_PROFILE_DO_NOT_RENDER");
  });
});
