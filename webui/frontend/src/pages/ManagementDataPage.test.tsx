import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { favorabilityDisplayClass, GroupAliasesPanel, GroupFavorabilityPanel, GroupMembersPanel } from "./ManagementDataPage";

describe("favorability display", () => {
  it("marks only negative scores with the existing red error class", () => {
    expect(favorabilityDisplayClass(-0.01)).toBe("state-error");
    expect(favorabilityDisplayClass(0)).toBe("");
  });

  it("renders group member level and score with a red negative cell only", () => {
    const { container } = render(<GroupMembersPanel profiles={[
      { user_id: "1", nickname: "甲", favorability: { level: "厌恶", score: -80 } },
      { user_id: "2", nickname: "乙", favorability: { level: "初见", score: 0 } },
    ]} />);
    expect(screen.getByText("厌恶 · -80")).toHaveClass("state-error");
    expect(screen.getByText("初见 · 0")).not.toHaveClass("state-error");
    expect(container.querySelectorAll("td.state-error")).toHaveLength(1);
  });

  it("keeps aliases and alias editing controls alongside member favorability", () => {
    render(<GroupAliasesPanel aliases={[{ user_id: "1", aliases: "甲同学", note: "常用称呼" }]} memberId="" aliasText="" pending={false} onMemberIdChange={() => {}} onAliasTextChange={() => {}} onSave={() => {}} onDelete={() => {}} />);
    expect(screen.getByText("成员别名（1）")).toBeInTheDocument();
    expect(screen.getByText("甲同学")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存别名" })).toBeInTheDocument();
  });

  it("renders a negative group relationship with signed counters and biases", () => {
    const { container } = render(<GroupFavorabilityPanel eyebrow="RELATION / FAVORABILITY" favorability={{ score: -80, level: "厌恶", score_min: -100, score_max: 100, daily_positive_count: 2, daily_negative_count: 3, daily_net_count: -1, behavior_policy: { random_reply_add: -0.2, group_idle_add: -0.16 } }} />);
    expect(screen.getByText("-80 / -100..100")).toHaveClass("state-error");
    expect(screen.getByText("+2")).toBeInTheDocument();
    expect(screen.getByText("-3")).toBeInTheDocument();
    expect(screen.getByText("-0.2")).toBeInTheDocument();
    expect(container.textContent).toContain("厌恶");
    expect(container.textContent).toContain("RELATION / FAVORABILITY");
  });
});
