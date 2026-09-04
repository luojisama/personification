import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resources } from "@/api/resources";
import type {
  GroupPeerBotBusinessState,
  OperationDiagnostic,
} from "@/api/types";
import GroupPeerBotsPanel from "./GroupPeerBotsPanel.vue";
import {
  composePeerBotCommand,
  renderPeerBotCommandDryRun,
  validatePeerBotCommandDraft,
} from "./peerBotCommand";

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

const EMPTY_TEST_SCHEMA = JSON.stringify({
  type: "object",
  properties: {},
  required: [],
  additionalProperties: false,
});
const state: GroupPeerBotBusinessState = {
  group_id: "415442985",
  enabled: true,
  bots: [
    {
      user_id: "20002",
      nickname: "Usagi",
      status: "candidate",
      confidence: 0.93,
      source: "llm_observation",
      manual_override: false,
      evidence_tags: ["fixed_format", "explicit_command_reply"],
      command_ids: ["mc_say"],
      updated_at: 1,
    },
  ],
  commands: [
    {
      command_id: "mc_say",
      target_bot_id: "20002",
      full_template: ".mc say {message}",
      command_head: ".mc say",
      command_entry: ".mc",
      subcommands: ["say"],
      argument_template: "{message}",
      description: "向 Minecraft 在线玩家发送聊天消息",
      legacy_mode: false,
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
      auto_approved: false,
      evidence_count: 1,
      protocol_source: "llm_observation",
      version: 1,
      updated_at: 1,
    },
  ],
  discovery_suggestions: [
    {
      user_id: "20002",
      nickname: "Usagi",
      confidence: 0.93,
      source: "llm_observation",
      evidence_tags: ["fixed_format", "explicit_command_reply"],
      reason_code: "peer_bot_candidate",
    },
  ],
  max_command_chars: 500,
  policies: {
    max_calls_per_turn: 1,
    cooldown_seconds: 10,
    pending_ttl_seconds: 30,
    max_chain_depth: 1,
    auto_learn_approved_commands: false,
  },
  pending_count: 1,
  loop_protection: {
    pending_count: 1,
    recent_count: 1,
    cooldown_count: 1,
    max_chain_depth: 1,
    diagnostics: {},
  },
  recent_invocations: [
    {
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
    },
  ],
  observer: {
    enabled: true,
    pending_messages: 2,
    pending_users: 1,
  },
  updated_at: 1,
  diagnostic_code: "peer_bot_state_ready",
};

function mountPanel(props = { groupId: "415442985" }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return mount(GroupPeerBotsPanel, {
    props,
    global: {
      plugins: [[VueQueryPlugin, { queryClient }]],
    },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GroupPeerBotsPanel.vue", () => {
  it("renders structured authorization, commands and invocation summaries without raw bodies", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.text()).toContain("Usagi");
    expect(wrapper.text()).toContain("93%");
    expect(wrapper.text()).toContain(".mc say {message}");
    expect(wrapper.text()).toContain("pb_safe_tracking");
    expect(wrapper.text()).not.toContain("PRIVATE RAW CHAT");
    expect(wrapper.text()).not.toContain("full_command");
  });

  it("marks an invalid template editor with accessible error attributes", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const wrapper = mountPanel();
    await flushPromises();

    const editButton = wrapper.findAll("button").find((b) => b.text().includes("编辑 / Dry-run"));
    expect(editButton).toBeDefined();
    await editButton!.trigger("click");

    const argumentInput = wrapper.findAll("input").find((input) => {
      return (input.element as HTMLInputElement).placeholder === "{message}";
    });
    expect(argumentInput).toBeDefined();

    await argumentInput!.setValue(".mc say {message");

    expect(argumentInput!.attributes("aria-invalid")).toBe("true");
    expect(argumentInput!.attributes("aria-describedby")).toBe("peer-command-help peer-command-error");

    const alert = wrapper.find('[role="alert"]');
    expect(alert.exists()).toBe(true);
    expect(alert.text()).toContain("未闭合");
  });

  it("performs dry-run locally without calling a mutation resource", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const save = vi.spyOn(resources, "saveGroupPeerBotCommand");
    const wrapper = mountPanel();
    await flushPromises();

    const editButton = wrapper.findAll("button").find((b) => b.text().includes("编辑 / Dry-run"));
    await editButton!.trigger("click");

    const dryRunTextarea = wrapper.findAll("textarea").find((t) => {
      const parent = t.element.parentElement?.textContent;
      return parent?.includes("Dry-run 参数");
    });
    expect(dryRunTextarea).toBeDefined();
    await dryRunTextarea!.setValue('{"message":"大家好"}');

    const dryRunButton = wrapper.findAll("button").find((b) => b.text().includes("仅验证，不发送"));
    expect(dryRunButton).toBeDefined();
    await dryRunButton!.trigger("click");

    const preview = wrapper.find("output.peer-bot-preview");
    expect(preview.exists()).toBe(true);
    expect(preview.text()).toBe("本地校验通过，未发送任何 QQ 消息：.mc say 大家好");
    expect(save).not.toHaveBeenCalled();
  });

  it("approves a candidate and refreshes the query", async () => {
    const get = vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const approve = vi.spyOn(resources, "updateGroupPeerBotStatus").mockResolvedValue(operation);
    const wrapper = mountPanel();
    await flushPromises();

    const approveButton = wrapper.findAll("button").find((b) => b.text().includes("采纳为 Bot"));
    expect(approveButton).toBeDefined();
    await approveButton!.trigger("click");
    await flushPromises();

    expect(approve).toHaveBeenCalledWith("415442985", "20002", "approve", "Usagi");
    expect(get.mock.calls.length).toBeGreaterThan(1);
  });

  it("saves structured v2 command fields without sending a QQ command", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const save = vi.spyOn(resources, "saveGroupPeerBotCommand").mockResolvedValue(operation);
    const wrapper = mountPanel();
    await flushPromises();

    const editButton = wrapper.findAll("button").find((b) => b.text().includes("编辑 / Dry-run"));
    await editButton!.trigger("click");

    const descriptionInput = wrapper.findAll("input").find((input) => {
      return (input.element as HTMLInputElement).placeholder?.includes("向 Minecraft 在线玩家发送聊天消息");
    });
    expect(descriptionInput).toBeDefined();
    await descriptionInput!.setValue("在服务器里发言");

    const saveButton = wrapper.findAll("button").find((b) => b.text().includes("保存模板"));
    expect(saveButton).toBeDefined();
    await saveButton!.trigger("click");
    await flushPromises();

    expect(save).toHaveBeenCalledWith(
      "415442985",
      "20002",
      "mc_say",
      expect.objectContaining({
        full_template: ".mc say {message}",
        command_entry: ".mc",
        subcommands: ["say"],
        argument_template: "{message}",
        description: "在服务器里发言",
      }),
    );
  });

  it("persists the approved-Bot protocol auto-learning switch", async () => {
    vi.spyOn(resources, "groupPeerBots").mockResolvedValue(state);
    const save = vi.spyOn(resources, "updateGroupPeerBotSettings").mockResolvedValue(operation);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const wrapper = mountPanel();
    await flushPromises();

    const autoLearnLabel = wrapper.findAll("label.checkbox-label").find((l) => l.text().includes("自动学习已批准 Bot 的新协议"));
    expect(autoLearnLabel).toBeDefined();
    const autoLearnInput = autoLearnLabel!.find("input[type='checkbox']");
    await autoLearnInput.setValue(true);

    const savePolicyButton = wrapper.findAll("button").find((b) => b.text().includes("保存群级策略"));
    expect(savePolicyButton).toBeDefined();
    await savePolicyButton!.trigger("click");
    await flushPromises();

    expect(save).toHaveBeenCalledWith(
      "415442985",
      expect.objectContaining({ auto_learn_approved_commands: true }),
    );
    expect(window.confirm).toHaveBeenCalled();
  });
});

describe("validatePeerBotCommandDraft and peerBotCommand helpers", () => {
  it("composes entry, optional subcommands and argument template", () => {
    expect(
      composePeerBotCommand({
        mode: "structured",
        target_bot_id: "20002",
        command_id: "mc_say",
        full_template: "",
        command_entry: ".mc",
        subcommand_1: "say",
        subcommand_2: "",
        argument_template: "{message}",
        parameter_schema_text: EMPTY_TEST_SCHEMA,
        risk_level: "write",
        status: "approved",
      }),
    ).toBe(".mc say {message}");
  });

  it("uses the backend single-brace placeholder contract", () => {
    const valid = validatePeerBotCommandDraft({
      target_bot_id: "20002",
      command_id: "mc_say",
      full_template: ".mc say {message}",
      parameter_schema_text: JSON.stringify({
        type: "object",
        properties: { message: { type: "string" } },
        required: ["message"],
        additionalProperties: false,
      }),
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
      parameter_schema_text: JSON.stringify({
        type: "object",
        properties: { message: { type: "string" } },
        required: ["message"],
        additionalProperties: false,
      }),
      risk_level: "write" as const,
      status: "approved" as const,
    };
    expect(validatePeerBotCommandDraft(base).error).toContain("固定命令前缀");
    expect(
      validatePeerBotCommandDraft({ ...base, full_template: `/say ${"x".repeat(20)}` }, 10).error,
    ).toContain("10 个字符");
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
    expect(
      renderPeerBotCommandDryRun(draft, valid, '{"count":"3","mode":"public","note":"ok"}'),
    ).toContain("类型必须是 integer");
    expect(
      renderPeerBotCommandDryRun(draft, valid, '{"count":11,"mode":"public","note":"ok"}'),
    ).toContain("不能大于 10");
    expect(
      renderPeerBotCommandDryRun(draft, valid, '{"count":3,"mode":"other","note":"ok"}'),
    ).toContain("enum");
    expect(
      renderPeerBotCommandDryRun(draft, valid, '{"count":3,"mode":"public","note":"hello"}'),
    ).toContain("不能超过 4");
  });

  it("renders dollar values literally and never cascades into later placeholders", () => {
    const draft = {
      target_bot_id: "20002",
      command_id: "safe_replace",
      full_template: "/say {first} {later}",
      parameter_schema_text: JSON.stringify({
        type: "object",
        properties: { first: { type: "string" }, later: { type: "string" } },
        required: ["first", "later"],
        additionalProperties: false,
      }),
      risk_level: "write" as const,
      status: "approved" as const,
    };
    const valid = validatePeerBotCommandDraft(draft);
    expect(
      renderPeerBotCommandDryRun(draft, valid, '{"first":"$& {later}","later":"done"}'),
    ).toBe("本地校验通过，未发送任何 QQ 消息：/say $& {later} done");
  });
});
