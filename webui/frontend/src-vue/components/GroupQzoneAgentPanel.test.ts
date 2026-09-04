import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type { GroupQzoneAgentState, OperationDiagnostic } from "@/api/types";
import GroupQzoneAgentPanel from "./GroupQzoneAgentPanel.vue";

const state: GroupQzoneAgentState = {
  group_id: "415442985",
  global_enabled: true,
  qzone_enabled: true,
  settings: {
    enabled: true,
    group_daily_limit: 3,
    target_daily_limit: 1,
    target_cooldown_seconds: 1800,
  },
  limits: {
    group_daily_limit: 3,
    target_daily_limit: 1,
    target_cooldown_seconds: 1800,
  },
  quota: { used_today: 1, group_daily_limit: 3, target_daily_limit: 1 },
  recent_operations: [
    {
      operation_id: "qzs:safe-operation",
      action: "comment",
      status: "unknown",
      result_code: "dispatch_timeout",
      created_at: 1,
      updated_at: 2,
    },
  ],
};

const disabledState: GroupQzoneAgentState = {
  ...state,
  settings: {
    ...state.settings,
    enabled: false,
  },
};

const diagnostic: OperationDiagnostic = {
  ok: true,
  code: "qzone_agent_settings_saved",
  phase: "operation_complete",
  title: "已保存",
  message: "群级空间策略已保存",
  retryable: false,
  partial: false,
  outcome_unknown: false,
  warnings: [],
  steps: [],
};

function renderPanel(groupId = "415442985") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return mount(GroupQzoneAgentPanel, {
    props: { groupId },
    global: {
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});
describe("GroupQzoneAgentPanel", () => {
  it("shows three gates and only redacted operation fields, with no like/comment/test-send buttons", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    const wrapper = renderPanel();
    await flushPromises();

    expect(wrapper.text()).toContain("空间互动门禁");
    const gateStrip = wrapper.find('[aria-label="空间互动三重门禁"]');
    expect(gateStrip.exists()).toBe(true);
    expect(gateStrip.text()).toContain("QZone 总开关");
    expect(gateStrip.text()).toContain("Agent 全局开关");
    expect(gateStrip.text()).toContain("本群开关");

    expect(wrapper.text()).toContain("qzs:safe-operation");
    expect(wrapper.text()).toContain("dispatch_timeout");
    expect(wrapper.text()).toContain("评论");
    expect(wrapper.text()).toContain("unknown");
    expect(wrapper.text()).not.toMatch(/feed[_ ]?id|comment[_ ]?text|cookie|说说正文/i);

    const buttons = wrapper.findAll("button");
    const testSendButtons = buttons.filter((button) => /点赞|评论|试发/.test(button.text()));
    expect(testSendButtons.length).toBe(0);
  });

  it("validates against global limits with accessible Chinese feedback", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    const wrapper = renderPanel();
    await flushPromises();

    const limitInput = wrapper.find<HTMLInputElement>('input[max="3"]');
    await limitInput.setValue("4");
    await limitInput.trigger("input");

    expect(limitInput.attributes("aria-invalid")).toBe("true");
    expect(limitInput.attributes("aria-describedby")).toBe("qzone-agent-group-limit-error");

    const alert = wrapper.find('[role="alert"]');
    expect(alert.exists()).toBe(true);
    expect(alert.text()).toContain("0 到 3");

    const saveButton = wrapper.find("button.button");
    expect(saveButton.attributes("disabled")).toBeDefined();
  });

  it("confirms disable and saves bounded group policy", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    const saveSpy = vi.spyOn(resources, "updateGroupQzoneAgent").mockResolvedValue(diagnostic);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const wrapper = renderPanel();
    await flushPromises();

    const checkbox = wrapper.find<HTMLInputElement>('input[type="checkbox"]');
    await checkbox.setValue(false);
    await checkbox.trigger("change");

    const saveButton = wrapper.find("button.button");
    await saveButton.trigger("click");
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalledWith("确认停用本群 QQ 空间 Agent 互动？");
    expect(saveSpy).toHaveBeenCalledWith("415442985", {
      enabled: false,
      group_daily_limit: 3,
      target_daily_limit: 1,
      target_cooldown_seconds: 1800,
    });
  });

  it("confirms enable when changing from disabled and saves bounded group policy", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(disabledState);
    const saveSpy = vi.spyOn(resources, "updateGroupQzoneAgent").mockResolvedValue(diagnostic);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const wrapper = renderPanel();
    await flushPromises();

    const checkbox = wrapper.find<HTMLInputElement>('input[type="checkbox"]');
    await checkbox.setValue(true);
    await checkbox.trigger("change");

    const saveButton = wrapper.find("button.button");
    await saveButton.trigger("click");
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalledWith("确认启用本群 QQ 空间 Agent 互动？");
    expect(saveSpy).toHaveBeenCalledWith("415442985", {
      enabled: true,
      group_daily_limit: 3,
      target_daily_limit: 1,
      target_cooldown_seconds: 1800,
    });
  });

  it("performs no mutation when disable confirmation is cancelled", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    const saveSpy = vi.spyOn(resources, "updateGroupQzoneAgent").mockResolvedValue(diagnostic);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    const wrapper = renderPanel();
    await flushPromises();

    const checkbox = wrapper.find<HTMLInputElement>('input[type="checkbox"]');
    await checkbox.setValue(false);
    await checkbox.trigger("change");

    const saveButton = wrapper.find("button.button");
    await saveButton.trigger("click");
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalled();
    expect(saveSpy).not.toHaveBeenCalled();
  });

  it("performs no mutation when enable confirmation is cancelled", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(disabledState);
    const saveSpy = vi.spyOn(resources, "updateGroupQzoneAgent").mockResolvedValue(diagnostic);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    const wrapper = renderPanel();
    await flushPromises();

    const checkbox = wrapper.find<HTMLInputElement>('input[type="checkbox"]');
    await checkbox.setValue(true);
    await checkbox.trigger("change");

    const saveButton = wrapper.find("button.button");
    await saveButton.trigger("click");
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalled();
    expect(saveSpy).not.toHaveBeenCalled();
  });
});
