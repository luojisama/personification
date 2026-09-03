import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";

import GroupAliasesPanel from "./subcomponents/GroupAliasesPanel.vue";
import GroupFavorabilityPanel from "./subcomponents/GroupFavorabilityPanel.vue";
import GroupMembersPanel from "./subcomponents/GroupMembersPanel.vue";
import { favorabilityDisplayClass } from "./subcomponents/managementHelpers";

describe("favorability display & management subcomponents", () => {
  it("marks only negative scores with the existing red error class", () => {
    expect(favorabilityDisplayClass(-0.01)).toBe("state-error");
    expect(favorabilityDisplayClass(0)).toBe("");
  });

  it("renders group member level and score with a red negative cell only", () => {
    const wrapper = mount(GroupMembersPanel, {
      props: {
        profiles: [
          { user_id: "1", nickname: "甲", favorability: { level: "厌恶", score: -80 } },
          { user_id: "2", nickname: "乙", favorability: { level: "初见", score: 0 } },
        ],
      },
    });
    expect(wrapper.text()).toContain("厌恶 · -80");
    expect(wrapper.text()).toContain("初见 · 0");
    const errorCells = wrapper.findAll("td.state-error");
    expect(errorCells).toHaveLength(1);
    expect(errorCells[0]?.text()).toContain("-80");
  });

  it("keeps aliases and alias editing controls alongside member favorability", () => {
    const wrapper = mount(GroupAliasesPanel, {
      props: {
        aliases: [{ user_id: "1", aliases: "甲同学", note: "常用称呼" }],
        memberId: "",
        aliasText: "",
        pending: false,
      },
    });
    expect(wrapper.text()).toContain("成员别名（1）");
    expect(wrapper.text()).toContain("甲同学");
    const saveBtn = wrapper.find("button");
    expect(saveBtn.text()).toContain("保存别名");
  });

  it("renders a negative group relationship with signed counters and biases", () => {
    const wrapper = mount(GroupFavorabilityPanel, {
      props: {
        eyebrow: "RELATION / FAVORABILITY",
        favorability: { score: -80, level: "厌恶", score_min: -100, score_max: 100, daily_positive_count: 2, daily_negative_count: 3, daily_net_count: -1, behavior_policy: { random_reply_add: -0.2, group_idle_add: -0.16 } },
      },
    });
    expect(wrapper.find(".state-error").text()).toBe("-80 / -100..100");
    expect(wrapper.text()).toContain("+2");
    expect(wrapper.text()).toContain("-3");
    expect(wrapper.text()).toContain("-0.2");
    expect(wrapper.text()).toContain("厌恶");
    expect(wrapper.text()).toContain("RELATION / FAVORABILITY");
  });
});
