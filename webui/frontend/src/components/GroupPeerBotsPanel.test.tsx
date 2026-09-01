import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resources } from "../api/resources";
import type { GroupPeerBotBusinessState, OperationDiagnostic } from "../api/types";
import { GroupPeerBotsPanel, renderPeerBotCommandDryRun, validatePeerBotCommandDraft } from "./GroupPeerBotsPanel";

const operation: OperationDiagnostic = {
  ok: true,
  code: "peer_bot_status_saved",
  phase: "operation_complete",
  title: "已保存",
  message: "状态已保存",
  retryable: false,
  partial: false,
  outcome_unknown: false,
  warnings: [],
  steps: [],
};

const state: GroupPeerBotBusinessState = {
  group_id: "415442985",
  enabled: true,
  bots: [{
    user_id: "20002",
    nickname: "Usagi",
    status: "candidate",
    confidence: 0.93,
    source: "llm_observation",
    manual_override: false,
    evidence_tags: ["fixed_format", "explicit_command_reply"],
    command_ids: ["mc_say"],
    updated_at: 1,
  }],
  commands: [{
    command_id: "mc_say",
    target_bot_id: "20002",
    full_template: ".mc say {message}",
    command_head: ".mc say",
    parameter_schema: {
      type: "object",
      properties: { message: { type: "string", maxLength: 160 } },
      required: ["message"],
      additionalProperties: false,
    },
    risk_level: "write",
    status: "candidate",
    source: "llm_observation",
    manual_override: false,
    version: 1,
    updated_at: 1,
  }],
  discovery_suggestions: [{
    user_id: "20002",
    nickname: "Usagi",
    confidence: 0.93,
    source: "llm_observation",
    evidence_tags: ["fixed_format", "explicit_command_reply"],
    reason_code: "peer_bot_candidate",
  }],
  max_command_chars: 500,
  policies: { max_calls_per_turn: 1, cooldown_seconds: 10, pending_ttl_seconds: 30, max_chain_depth: 1 },
  pending_count: 1,
  loop_protection: { pending_count: 1, recent_count: 1, cooldown_count: 1, max_chain_depth: 1, diagnostics: {} },
  recent_invocations: [{
    target_bot_id: "20002",
    tracking_id: "pb_safe_tracking",
    operation_id: "peerbot:safe-operation",
    command_id: "mc_say",
    send_status: "sent",
    status: "pending",
    depth: 1,
    reply_message_count: 0,
    elapsed_ms: 25,
    diagnostic_code: "peer_bot_dispatch_sent",
  }],
  observer: { enabled: true, pending_messages: 2, pending_users: 1 },
  updated_at: 1,
  diagnostic_code: "peer_bot_state_ready",
};

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><GroupPeerBotsPanel groupId="415442985" /></QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("GroupPeerBotsPanel", () => {
  it("renders structured authorization, commands and invocation summaries without raw bodies", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    renderPanel();

    expect(await screen.findAllByText("Usagi")).not.toHaveLength(0);
    expect(screen.getAllByText("93%").length).toBeGreaterThan(0);
    expect(screen.getByText(".mc say {message}")).toBeInTheDocument();
    expect(screen.getByText("pb_safe_tracking")).toBeInTheDocument();
    expect(screen.queryByText(/PRIVATE RAW CHAT/i)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("full_command");
  });

  it("marks an invalid template editor with accessible error attributes", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "编辑 / Dry-run" }));

    const template = screen.getByLabelText("完整命令模板");
    fireEvent.change(template, { target: { value: ".mc say {message" } });

    expect(template).toHaveAttribute("aria-invalid", "true");
    expect(template).toHaveAttribute("aria-describedby", "peer-command-help peer-command-error");
    expect(screen.getByRole("alert")).toHaveTextContent("未闭合");
  });

  it("performs dry-run locally without calling a mutation resource", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const save = vi.spyOn(resources, "saveGroupPeerBotCommand");
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "编辑 / Dry-run" }));
    fireEvent.change(screen.getByLabelText("Dry-run 参数（JSON）"), { target: { value: '{"message":"大家好"}' } });
    fireEvent.click(screen.getByRole("button", { name: "仅验证，不发送" }));

    expect(await screen.findByText("本地校验通过，未发送任何 QQ 消息：.mc say 大家好")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  });

  it("approves a candidate and refreshes the query", async () => {
    const get = vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const approve = vi.spyOn(resources, "updateGroupPeerBotStatus").mockResolvedValue(operation);
    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: "采纳为 Bot" }));

    await waitFor(() => expect(approve).toHaveBeenCalledWith("415442985", "20002", "approve", "Usagi"));
    await waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(1));
  });
});

describe("validatePeerBotCommandDraft", () => {
  it("uses the backend single-brace placeholder contract", () => {
    const valid = validatePeerBotCommandDraft({
      target_bot_id: "20002",
      command_id: "mc_say",
      full_template: ".mc say {message}",
      parameter_schema_text: JSON.stringify({ type: "object", properties: { message: { type: "string" } }, required: ["message"], additionalProperties: false }),
      risk_level: "write",
      status: "approved",
    });
    expect(valid.error).toBe("");
    expect(valid.placeholders).toEqual(["message"]);
  });

  it("rejects templates without a literal command head or beyond the effective limit", () => {
    const base = {
      target_bot_id: "20002",
      command_id: "bad_head",
      full_template: "{message}",
      parameter_schema_text: JSON.stringify({ type: "object", properties: { message: { type: "string" } }, required: ["message"], additionalProperties: false }),
      risk_level: "write" as const,
      status: "approved" as const,
    };
    expect(validatePeerBotCommandDraft(base).error).toContain("固定命令前缀");
    expect(validatePeerBotCommandDraft({ ...base, full_template: `/say ${"x".repeat(20)}` }, 10).error).toContain("10 个字符");
  });

  it("dry-run enforces schema type, range, enum and maxLength constraints", () => {
    const draft = {
      target_bot_id: "20002",
      command_id: "roll",
      full_template: "/roll {count} {mode} {note}",
      parameter_schema_text: JSON.stringify({
        type: "object",
        properties: {
          count: { type: "integer", minimum: 1, maximum: 10 },
          mode: { type: "string", enum: ["public", "private"] },
          note: { type: "string", maxLength: 4 },
        },
        required: ["count", "mode", "note"],
        additionalProperties: false,
      }),
      risk_level: "write" as const,
      status: "approved" as const,
    };
    const valid = validatePeerBotCommandDraft(draft);
    expect(renderPeerBotCommandDryRun(draft, valid, '{"count":"3","mode":"public","note":"ok"}')).toContain("类型必须是 integer");
    expect(renderPeerBotCommandDryRun(draft, valid, '{"count":11,"mode":"public","note":"ok"}')).toContain("不能大于 10");
    expect(renderPeerBotCommandDryRun(draft, valid, '{"count":3,"mode":"other","note":"ok"}')).toContain("enum");
    expect(renderPeerBotCommandDryRun(draft, valid, '{"count":3,"mode":"public","note":"hello"}')).toContain("不能超过 4");
  });

  it("renders dollar values literally and never cascades into later placeholders", () => {
    const draft = {
      target_bot_id: "20002",
      command_id: "safe_replace",
      full_template: "/say {first} {later}",
      parameter_schema_text: JSON.stringify({ type: "object", properties: { first: { type: "string" }, later: { type: "string" } }, required: ["first", "later"], additionalProperties: false }),
      risk_level: "write" as const,
      status: "approved" as const,
    };
    const valid = validatePeerBotCommandDraft(draft);
    expect(renderPeerBotCommandDryRun(draft, valid, '{"first":"$& {later}","later":"done"}')).toBe("本地校验通过，未发送任何 QQ 消息：/say $& {later} done");
  });
});
