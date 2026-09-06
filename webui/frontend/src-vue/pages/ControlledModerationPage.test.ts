import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import ControlledModerationPage from "./ControlledModerationPage.vue";

const resourceMocks = vi.hoisted(() => ({ moderationStatus: vi.fn(), moderationIncidents: vi.fn(), releaseModerationOperation: vi.fn() }));
vi.mock("@/api/resources", () => ({ resources: resourceMocks }));

const incident = {
  platform: "onebot", bot_id: "bot", group_id: "10001", target_id: "20002", incident: "incident-long-id",
  updated_at: 1_700_000_000, warning_count: 2, warning_message_ids: ["warning-1", "warning-2"], evidence_message_ids: ["source-3"],
  operation_id: "operation-1", status: "sent", minutes: 8,
};

describe("ControlledModerationPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resourceMocks.moderationStatus.mockResolvedValue({ enabled: true, authorized_groups: ["10001"] });
    resourceMocks.moderationIncidents.mockResolvedValue({ items: [incident], page: 1, page_size: 20, total: 41, total_pages: 3 });
    resourceMocks.releaseModerationOperation.mockResolvedValue({ ok: true, code: "moderation_release_released", operation_id: "operation-1" });
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  async function render(path = "/persona/controlled-moderation/incidents") {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/persona/controlled-moderation/incidents", component: ControlledModerationPage }, { path: "/operations/config/general", component: { template: "<div/>" } }] });
    await router.push(path); await router.isReady();
    const wrapper = mount(ControlledModerationPage, { global: { plugins: [router, [VueQueryPlugin, { queryClient: new QueryClient({ defaultOptions: { queries: { retry: false } } }) }]] } });
    await vi.waitFor(() => expect(wrapper.text()).toContain("受控禁言"));
    await vi.waitFor(() => expect(wrapper.text()).toContain("2 / 2 轮已确认"));
    return { wrapper, router };
  }

  it("shows effective switch, authorized groups, warning evidence and complete pagination", async () => {
    const { wrapper } = await render();
    expect(wrapper.text()).toContain("已启用");
    expect(wrapper.text()).toContain("10001");
    expect(wrapper.text()).toContain("提醒消息引用：warning-1、warning-2");
    expect(wrapper.findComponent({ name: "Pagination" }).exists()).toBe(true);
    expect(wrapper.text()).toContain("首页");
    expect(wrapper.text()).toContain("跳转页码");
    expect(wrapper.text()).toContain("/ 3 页");
  });

  it("posts only the operation identifier for a sent incident and shows server feedback", async () => {
    const { wrapper } = await render();
    await wrapper.get("button.button-danger").trigger("click");
    await vi.waitFor(() => expect(resourceMocks.releaseModerationOperation).toHaveBeenCalledWith("operation-1"));
    expect(resourceMocks.releaseModerationOperation.mock.calls[0]).toHaveLength(1);
    await vi.waitFor(() => expect(wrapper.text()).toContain("moderation_release_released"));
  });

  it("does not render release action for warning-only incidents", async () => {
    resourceMocks.moderationIncidents.mockResolvedValueOnce({ items: [{ ...incident, status: "warning_only", operation_id: "", minutes: 0 }], page: 1, page_size: 20, total: 1, total_pages: 1 });
    const { wrapper } = await render();
    expect(wrapper.find("button.button-danger").exists()).toBe(false);
    expect(wrapper.text()).toContain("仅记录提醒，不可解除");
  });

  it("marks expired reminders as unusable rather than current confirmed evidence", async () => {
    resourceMocks.moderationIncidents.mockResolvedValueOnce({ items: [{ ...incident, expires_at: 1, status: "warning_only", operation_id: "" }], page: 1, page_size: 20, total: 1, total_pages: 1 });
    const { wrapper } = await render();
    expect(wrapper.text()).toContain("2 / 2 轮已确认（已过期）");
    expect(wrapper.text()).toContain("提醒窗口已过期，不能作为当前处罚依据");
  });
});
