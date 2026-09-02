import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resources } from "../api/resources";
import type { GroupQzoneAgentState, OperationDiagnostic } from "../api/types";
import { GroupQzoneAgentPanel } from "./GroupQzoneAgentPanel";

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
  recent_operations: [{
    operation_id: "qzs:safe-operation",
    action: "comment",
    status: "unknown",
    result_code: "dispatch_timeout",
    created_at: 1,
    updated_at: 2,
  }],
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

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><GroupQzoneAgentPanel groupId="415442985" /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("GroupQzoneAgentPanel", () => {
  it("shows three gates and only redacted operation fields", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    renderPanel();

    expect(await screen.findByText("空间互动门禁")).toBeInTheDocument();
    expect(screen.getByLabelText("空间互动三重门禁")).toHaveTextContent("QZone 总开关");
    expect(screen.getByText("qzs:safe-operation")).toBeInTheDocument();
    expect(screen.getByText("dispatch_timeout")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/feed[_ ]?id|comment[_ ]?text|cookie|说说正文/i);
    expect(screen.queryByRole("button", { name: /点赞|评论|试发/ })).not.toBeInTheDocument();
  });

  it("validates against global limits with accessible Chinese feedback", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    renderPanel();
    const limit = await screen.findByLabelText("本群每日写入上限");
    fireEvent.change(limit, { target: { value: "4" } });

    expect(limit).toHaveAttribute("aria-invalid", "true");
    expect(limit).toHaveAttribute("aria-describedby", "qzone-agent-group-limit-error");
    expect(screen.getByRole("alert")).toHaveTextContent("0 到 3");
    expect(screen.getByRole("button", { name: "保存群级空间策略" })).toBeDisabled();
  });

  it("confirms disable and saves bounded group policy", async () => {
    vi.spyOn(resources, "groupQzoneAgent").mockResolvedValue(state);
    const save = vi.spyOn(resources, "updateGroupQzoneAgent").mockResolvedValue(diagnostic);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPanel();
    fireEvent.click(await screen.findByLabelText("启用本群空间互动"));
    fireEvent.click(screen.getByRole("button", { name: "保存群级空间策略" }));

    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    await waitFor(() => expect(save).toHaveBeenCalledWith("415442985", {
      enabled: false,
      group_daily_limit: 3,
      target_daily_limit: 1,
      target_cooldown_seconds: 1800,
    }));
  });
});
