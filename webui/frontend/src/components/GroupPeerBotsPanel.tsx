import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { diagnosticFromError, safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type {
  GroupMemberOption,
  PeerBotCommandTemplate,
  PeerBotRiskLevel,
  PeerBotStatus,
} from "../api/types";
import { formatDateTime } from "../lib/format";
import { DiagnosticPanel, useDiagnosticHistory } from "./DiagnosticPanel";
import { EmptyState, Panel } from "./Panel";
import { QueryBoundary } from "./QueryBoundary";
import { StateBadge } from "./StateBadge";

type CommandDraft = {
  mode?: "structured" | "legacy";
  target_bot_id: string;
  command_id: string;
  full_template: string;
  command_entry?: string;
  subcommand_1?: string;
  subcommand_2?: string;
  argument_template?: string;
  description?: string;
  parameter_schema_text: string;
  risk_level: PeerBotRiskLevel;
  status: PeerBotStatus;
};

type LocalValidation = {
  error: string;
  field: "target_bot_id" | "command_id" | "command_entry" | "argument_template" | "full_template" | "parameter_schema" | null;
  placeholders: string[];
  schema: PeerBotCommandTemplate["parameter_schema"] | null;
};

const EMPTY_SCHEMA = JSON.stringify({
  type: "object",
  properties: {},
  required: [],
  additionalProperties: false,
}, null, 2);

export function composePeerBotCommand(draft: CommandDraft): string {
  if ((draft.mode ?? "legacy") === "legacy") return draft.full_template.trim();
  return [
    draft.command_entry?.trim(),
    draft.subcommand_1?.trim(),
    draft.subcommand_2?.trim(),
    draft.argument_template?.trim(),
  ].filter(Boolean).join(" ");
}

function templateFields(template: string): { fields: string[]; error: string } {
  const fields: string[] = [];
  for (let index = 0; index < template.length;) {
    const char = template[index];
    if (char === "{" && template[index + 1] === "{") {
      index += 2;
      continue;
    }
    if (char === "}" && template[index + 1] === "}") {
      index += 2;
      continue;
    }
    if (char === "}") return { fields: [], error: "模板包含未配对的右花括号。" };
    if (char !== "{") {
      index += 1;
      continue;
    }
    const end = template.indexOf("}", index + 1);
    if (end < 0) return { fields: [], error: "模板包含未闭合的参数占位符。" };
    const field = template.slice(index + 1, end);
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(field)) {
      return { fields: [], error: "参数名只能使用字母、数字和下划线，且不能以数字开头。" };
    }
    if (fields.includes(field)) return { fields: [], error: `参数 {${field}} 在模板中重复出现。` };
    fields.push(field);
    index = end + 1;
  }
  return { fields, error: "" };
}

function literalCommandHead(template: string): string {
  let literal = "";
  for (let index = 0; index < template.length;) {
    if (template[index] === "{" && template[index + 1] === "{") {
      literal += "{";
      index += 2;
      continue;
    }
    if (template[index] === "}" && template[index + 1] === "}") {
      literal += "}";
      index += 2;
      continue;
    }
    if (template[index] === "{") break;
    literal += template[index];
    index += 1;
  }
  return literal.replace(/\s+/g, " ").trim();
}

export function validatePeerBotCommandDraft(draft: CommandDraft, maxCommandChars = 500): LocalValidation {
  if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(draft.target_bot_id)) {
    return { error: "请选择有效的目标 Bot。", field: "target_bot_id", placeholders: [], schema: null };
  }
  if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(draft.command_id)) {
    return { error: "命令 ID 只能使用字母、数字、点、冒号、下划线或连字符。", field: "command_id", placeholders: [], schema: null };
  }
  if ((draft.mode ?? "legacy") === "structured" && !draft.command_entry?.trim()) {
    return { error: "结构化模式必须填写命令入口，例如 .mc 或 /抽卡。", field: "command_entry", placeholders: [], schema: null };
  }
  const template = composePeerBotCommand(draft);
  if (!template || /[\r\n\u0000-\u001f\u007f-\u009f]/.test(template)) {
    return { error: "完整命令模板必须是单行且不能包含控制字符。", field: (draft.mode ?? "legacy") === "structured" ? "argument_template" : "full_template", placeholders: [], schema: null };
  }
  if (template.length > Math.max(1, Math.min(4000, maxCommandChars))) {
    return { error: `完整命令模板不能超过 ${maxCommandChars} 个字符。`, field: (draft.mode ?? "legacy") === "structured" ? "argument_template" : "full_template", placeholders: [], schema: null };
  }
  const parsedTemplate = templateFields(template);
  if (parsedTemplate.error) return { error: parsedTemplate.error, field: (draft.mode ?? "legacy") === "structured" ? "argument_template" : "full_template", placeholders: [], schema: null };
  const commandHead = literalCommandHead(template);
  if (!commandHead) return { error: "模板必须以固定命令前缀开头，不能以参数占位符开头。", field: (draft.mode ?? "legacy") === "structured" ? "command_entry" : "full_template", placeholders: [], schema: null };
  try {
    const parsed: unknown = JSON.parse(draft.parameter_schema_text);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("schema 必须是 JSON 对象");
    const schema = parsed as Record<string, unknown>;
    if (schema.type !== "object" || schema.additionalProperties !== false) {
      throw new Error("schema 必须声明 type=object 且 additionalProperties=false");
    }
    const properties = schema.properties;
    const required = schema.required;
    if (!properties || typeof properties !== "object" || Array.isArray(properties) || !Array.isArray(required)) {
      throw new Error("schema 必须包含 properties 对象和 required 数组");
    }
    const propertyNames = Object.keys(properties as Record<string, unknown>).sort();
    const placeholderNames = [...parsedTemplate.fields].sort();
    if (propertyNames.join("\u0000") !== placeholderNames.join("\u0000")) {
      throw new Error("properties 必须与模板占位符完全一致");
    }
    if (required.some((name) => typeof name !== "string" || !propertyNames.includes(name))) {
      throw new Error("required 包含未声明参数");
    }
    for (const [name, definition] of Object.entries(properties as Record<string, unknown>)) {
      if (!definition || typeof definition !== "object" || Array.isArray(definition)) throw new Error(`${name} 的定义无效`);
      const type = (definition as Record<string, unknown>).type;
      if (!new Set(["string", "integer", "number", "boolean"]).has(String(type))) {
        throw new Error(`${name} 使用了不支持的参数类型`);
      }
      const constraint = definition as Record<string, unknown>;
      if (constraint.enum !== undefined) {
        if (!Array.isArray(constraint.enum) || constraint.enum.length < 1 || constraint.enum.length > 30) throw new Error(`${name} 的 enum 必须包含 1 到 30 个值`);
        if (constraint.enum.some((value) => !valueMatchesPeerBotType(value, String(type)))) throw new Error(`${name} 的 enum 值类型与参数类型不一致`);
      }
    }
    return {
      error: "",
      field: null,
      placeholders: parsedTemplate.fields,
      schema: parsed as PeerBotCommandTemplate["parameter_schema"],
    };
  } catch (error) {
    return {
      error: `参数 schema 无效：${error instanceof Error ? error.message : "无法解析"}`,
      field: "parameter_schema",
      placeholders: parsedTemplate.fields,
      schema: null,
    };
  }
}

export function renderPeerBotCommandDryRun(draft: CommandDraft, validation: LocalValidation, argumentsText: string): string {
  if (validation.error || !validation.schema) return validation.error || "命令配置无效。";
  let args: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(argumentsText || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("参数必须是 JSON 对象");
    args = parsed as Record<string, unknown>;
  } catch (error) {
    return `Dry-run 参数无效：${error instanceof Error ? error.message : "无法解析"}`;
  }
  const allowed = new Set(validation.placeholders);
  if (Object.keys(args).some((key) => !allowed.has(key))) return "Dry-run 参数包含 schema 未声明字段。";
  for (const field of validation.placeholders) if (!(field in args)) return `Dry-run 缺少模板参数：${field}`;
  for (const field of validation.placeholders) {
    const definition = validation.schema.properties[field];
    if (!definition) return `Dry-run 参数 schema 缺少字段：${field}`;
    const value = args[field];
    if (!valueMatchesPeerBotType(value, definition.type)) return `Dry-run 参数 ${field} 类型必须是 ${definition.type}。`;
    if (definition.type === "string" && definition.maxLength !== undefined && String(value).length > definition.maxLength) return `Dry-run 参数 ${field} 不能超过 ${definition.maxLength} 个字符。`;
    if ((definition.type === "integer" || definition.type === "number") && definition.minimum !== undefined && Number(value) < definition.minimum) return `Dry-run 参数 ${field} 不能小于 ${definition.minimum}。`;
    if ((definition.type === "integer" || definition.type === "number") && definition.maximum !== undefined && Number(value) > definition.maximum) return `Dry-run 参数 ${field} 不能大于 ${definition.maximum}。`;
    if (definition.enum && !definition.enum.some((candidate) => Object.is(candidate, value))) return `Dry-run 参数 ${field} 不在允许的 enum 中。`;
    if (/[\r\n\u0000-\u001f\u007f-\u009f]/.test(String(value))) return `Dry-run 参数 ${field} 包含控制字符。`;
  }
  const template = composePeerBotCommand(draft);
  let rendered = "";
  for (let index = 0; index < template.length;) {
    if (template[index] === "{" && template[index + 1] === "{") {
      rendered += "{";
      index += 2;
      continue;
    }
    if (template[index] === "}" && template[index + 1] === "}") {
      rendered += "}";
      index += 2;
      continue;
    }
    if (template[index] !== "{") {
      rendered += template[index];
      index += 1;
      continue;
    }
    const end = template.indexOf("}", index + 1);
    const field = template.slice(index + 1, end);
    rendered += field in args ? String(args[field]) : `{${field}}`;
    index = end + 1;
  }
  return `本地校验通过，未发送任何 QQ 消息：${rendered}`;
}

function valueMatchesPeerBotType(value: unknown, type: string): boolean {
  if (type === "string") return typeof value === "string";
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "boolean") return typeof value === "boolean";
  return false;
}

function botTone(status: PeerBotStatus): "ok" | "warn" | "error" {
  return status === "approved" ? "ok" : status === "rejected" ? "error" : "warn";
}

function riskTone(risk: PeerBotRiskLevel): "ok" | "warn" | "error" | "info" {
  return risk === "read" ? "ok" : risk === "write" ? "info" : risk === "admin" ? "warn" : "error";
}

function emptyDraft(targetBotId = ""): CommandDraft {
  return {
    mode: "structured",
    target_bot_id: targetBotId,
    command_id: "",
    full_template: "",
    command_entry: "",
    subcommand_1: "",
    subcommand_2: "",
    argument_template: "",
    description: "",
    parameter_schema_text: EMPTY_SCHEMA,
    risk_level: "read",
    status: "candidate",
  };
}

function commandDraft(command: PeerBotCommandTemplate): CommandDraft {
  return {
    mode: command.legacy_mode ? "legacy" : "structured",
    target_bot_id: command.target_bot_id,
    command_id: command.command_id,
    full_template: command.full_template,
    command_entry: command.command_entry,
    subcommand_1: command.subcommands[0] ?? "",
    subcommand_2: command.subcommands[1] ?? "",
    argument_template: command.argument_template,
    description: command.description,
    parameter_schema_text: JSON.stringify(command.parameter_schema, null, 2),
    risk_level: command.risk_level,
    status: command.status,
  };
}

function structuredFieldsFromLegacy(fullTemplate: string): Pick<CommandDraft, "command_entry" | "subcommand_1" | "subcommand_2" | "argument_template"> {
  const parts = fullTemplate.trim().split(/\s+/).filter(Boolean);
  const firstArgument = parts.findIndex((part) => part.includes("{"));
  const headEnd = firstArgument >= 0 ? firstArgument : Math.min(parts.length, 3);
  const subcommands = parts.slice(1, Math.min(headEnd, 3));
  return {
    command_entry: parts[0] ?? "",
    subcommand_1: subcommands[0] ?? "",
    subcommand_2: subcommands[1] ?? "",
    argument_template: parts.slice(Math.min(headEnd, 3)).join(" "),
  };
}

export function GroupPeerBotsPanel({ groupId, botId = "" }: { groupId: string; botId?: string }) {
  const history = useDiagnosticHistory(`group-peer-bots-${groupId}`);
  const query = useQuery({
    queryKey: ["group-peer-bots", groupId],
    queryFn: ({ signal }) => resources.groupPeerBots(groupId, signal),
    enabled: Boolean(groupId),
  });
  const data = query.data;
  const membersQuery = useQuery({
    queryKey: ["group-peer-bot-member-options", groupId, botId],
    queryFn: ({ signal }) => resources.groupMembers(groupId, botId, signal),
    enabled: Boolean(groupId && botId),
  });
  const [enabled, setEnabled] = useState(false);
  const [autoLearn, setAutoLearn] = useState(false);
  const [cooldown, setCooldown] = useState("10");
  const [ttl, setTtl] = useState("30");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [draft, setDraft] = useState<CommandDraft>(() => emptyDraft());
  const [argumentsText, setArgumentsText] = useState("{}");
  const [dryRunResult, setDryRunResult] = useState("");
  const [memberSearch, setMemberSearch] = useState("");

  useEffect(() => {
    if (!data) return;
    if (!settingsDirty) {
      setEnabled(data.enabled);
      setAutoLearn(Boolean(data.policies.auto_learn_approved_commands));
      setCooldown(String(data.policies.cooldown_seconds));
      setTtl(String(data.policies.pending_ttl_seconds));
    }
    setDraft((current) => current.target_bot_id ? current : emptyDraft(data.bots[0]?.user_id ?? ""));
  }, [data, settingsDirty]);

  useEffect(() => setSettingsDirty(false), [groupId]);

  const validation = useMemo(() => validatePeerBotCommandDraft(draft, data?.max_command_chars ?? 500), [data?.max_command_chars, draft]);
  const memberOptions = useMemo(() => {
    const options = new Map<string, GroupMemberOption>();
    for (const member of membersQuery.data?.members ?? []) {
      const userId = String(member.user_id ?? "").trim();
      if (userId) options.set(userId, member);
    }
    for (const bot of data?.bots ?? []) {
      if (!options.has(bot.user_id)) options.set(bot.user_id, { user_id: bot.user_id, nickname: bot.nickname });
    }
    const needle = memberSearch.trim().toLowerCase();
    return [...options.values()].filter((member) => {
      if (draft.target_bot_id && String(member.user_id) === draft.target_bot_id) return true;
      if (!needle) return true;
      return [member.user_id, member.card, member.nickname].some((value) => String(value ?? "").toLowerCase().includes(needle));
    });
  }, [data?.bots, draft.target_bot_id, memberSearch, membersQuery.data?.members]);
  const mutation = useMutation({
    mutationFn: async (action: { kind: string; [key: string]: unknown }) => {
      if (action.kind === "settings") return resources.updateGroupPeerBotSettings(groupId, action.body as { enabled: boolean; auto_learn_approved_commands: boolean; max_calls_per_turn: 1; cooldown_seconds: number; pending_ttl_seconds: number; max_chain_depth: 1 });
      if (action.kind === "bot") return resources.updateGroupPeerBotStatus(groupId, String(action.userId), action.action as "approve" | "reject" | "clear", String(action.nickname ?? ""));
      if (action.kind === "discover") return resources.discoverGroupPeerBots(groupId);
      if (action.kind === "reset") return resources.resetGroupPeerBotLoop(groupId);
      if (action.kind === "delete-command") return resources.deleteGroupPeerBotCommand(groupId, String(action.userId), String(action.commandId));
      const saveDraft = action.draft as CommandDraft;
      const saveSchema = action.schema as PeerBotCommandTemplate["parameter_schema"];
      const structured = (saveDraft.mode ?? "legacy") === "structured";
      return resources.saveGroupPeerBotCommand(groupId, saveDraft.target_bot_id, saveDraft.command_id, {
        full_template: composePeerBotCommand(saveDraft),
        ...(structured ? {
          command_entry: saveDraft.command_entry?.trim() ?? "",
          subcommands: [saveDraft.subcommand_1, saveDraft.subcommand_2].map((item) => item?.trim() ?? "").filter(Boolean),
          argument_template: saveDraft.argument_template?.trim() ?? "",
          description: saveDraft.description?.trim() ?? "",
        } : {}),
        parameter_schema: saveSchema,
        risk_level: saveDraft.risk_level,
        status: saveDraft.status,
      });
    },
    onSuccess: (result, action) => {
      history.record(safeDiagnostic(result));
      void query.refetch().then(() => {
        if (action.kind === "settings") setSettingsDirty(false);
      });
    },
    onError: (error) => history.record(diagnosticFromError(error)),
  });

  const saveSettings = () => {
    const cooldownValue = Number(cooldown);
    const ttlValue = Number(ttl);
    if (!Number.isFinite(cooldownValue) || cooldownValue < 0 || cooldownValue > 3600 || !Number.isFinite(ttlValue) || ttlValue < 1 || ttlValue > 600) return;
    if (!data?.policies.auto_learn_approved_commands && autoLearn && !window.confirm("启用后，高置信 read/write 新协议可能自动进入 Agent 能力目录；admin/dangerous 仍只会成为候选。确认启用？")) return;
    mutation.mutate({ kind: "settings", body: { enabled, auto_learn_approved_commands: autoLearn, max_calls_per_turn: 1, cooldown_seconds: cooldownValue, pending_ttl_seconds: ttlValue, max_chain_depth: 1 } });
  };

  const switchEditMode = (mode: "structured" | "legacy") => {
    setDraft((current) => {
      if (mode === "legacy") {
        return { ...current, mode, full_template: composePeerBotCommand(current) || current.full_template };
      }
      const fields = current.command_entry?.trim()
        ? {}
        : structuredFieldsFromLegacy(current.full_template);
      return { ...current, ...fields, mode };
    });
  };

  const generateSimpleSchema = () => {
    const parsed = templateFields(composePeerBotCommand(draft));
    if (parsed.error) {
      setDryRunResult(parsed.error);
      return;
    }
    const schema = {
      type: "object",
      properties: Object.fromEntries(parsed.fields.map((field) => [field, { type: "string", description: `${field} 参数` }])),
      required: parsed.fields,
      additionalProperties: false,
    };
    setDraft({ ...draft, parameter_schema_text: JSON.stringify(schema, null, 2) });
    setDryRunResult(parsed.fields.length ? "已按占位符生成简单参数定义；可在高级 schema 中继续填写类型、说明和边界。" : "当前命令没有参数，占位符 schema 已清空。" );
  };

  return <div className="page-stack peer-bot-page">
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {data && <>
        <div className="summary-grid">
          <Panel eyebrow="PEER BOT / POLICY" title="协作与循环保护">
            <div className="form-grid peer-bot-settings-grid">
              <label className="checkbox-label"><input type="checkbox" checked={enabled} onChange={(event) => { setEnabled(event.target.checked); setSettingsDirty(true); }} />启用本群外部 Bot 调用</label>
              <label className="checkbox-label"><input type="checkbox" checked={autoLearn} onChange={(event) => { setAutoLearn(event.target.checked); setSettingsDirty(true); }} />自动学习已批准 Bot 的新协议</label>
              <label>冷却秒数<input type="number" min="0" max="3600" value={cooldown} onChange={(event) => { setCooldown(event.target.value); setSettingsDirty(true); }} /></label>
              <label>回复等待 TTL<input type="number" min="1" max="600" value={ttl} onChange={(event) => { setTtl(event.target.value); setSettingsDirty(true); }} /></label>
            </div>
            <p className="muted-copy">自动学习只作用于已批准 Bot 的高置信 read/write 协议；不会覆盖管理员模板，admin/dangerous 永不自动批准。</p>
            <div className="inline-controls">
              <button className="button" type="button" disabled={mutation.isPending} onClick={() => (!data.enabled || enabled || window.confirm("确认停用本群 Peer Bot 调用？")) && saveSettings()}>保存群级策略</button>
              <button className="button button-danger" type="button" disabled={mutation.isPending} onClick={() => window.confirm("确认清除本群进程内 pending 与 cooldown？不会重发任何命令。") && mutation.mutate({ kind: "reset" })}>复位循环保护</button>
            </div>
            <dl className="compact-kv"><dt>单回合上限</dt><dd>1 次</dd><dt>跨 Bot 深度</dt><dd>1 层</dd><dt>Pending</dt><dd>{data.pending_count}</dd><dt>冷却项</dt><dd>{data.loop_protection.cooldown_count}</dd><dt>观察微批</dt><dd>{data.observer.pending_messages} 条 / {data.observer.pending_users} 个用户</dd></dl>
          </Panel>
          <Panel eyebrow="PEER BOT / DISCOVERY" title="LLM 发现建议" action={<button className="button button-secondary" type="button" disabled={mutation.isPending || !data.observer.enabled} onClick={() => window.confirm("只评估当前群已缓冲的观察微批，不会自动授权。继续？") && mutation.mutate({ kind: "discover" })}>发现一次</button>}>
            {data.discovery_suggestions.length ? <ul className="business-list">{data.discovery_suggestions.map((item) => <li key={item.user_id}><strong>{item.nickname || item.user_id} · {(item.confidence * 100).toFixed(0)}%</strong><span>{item.evidence_tags.join(" / ") || "insufficient_context"}</span><code>{item.reason_code}</code><div className="inline-controls"><button className="button" type="button" disabled={mutation.isPending} onClick={() => mutation.mutate({ kind: "bot", userId: item.user_id, nickname: item.nickname, action: "approve" })}>采纳为 Bot</button><button className="button button-secondary" type="button" disabled={mutation.isPending} onClick={() => window.confirm(`确认忽略候选 ${item.user_id}？`) && mutation.mutate({ kind: "bot", userId: item.user_id, action: "reject" })}>忽略建议</button></div></li>)}</ul> : <EmptyState code="peer_bot_suggestions_empty">当前没有待审核的结构化建议。</EmptyState>}
          </Panel>
        </div>

        <Panel eyebrow="PEER BOT / REGISTRY" title={`识别与授权（${data.bots.length}）`}>
          {data.bots.length ? <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>昵称</th><th>Bot ID</th><th>置信度</th><th>来源</th><th>状态</th><th>证据标签</th><th>操作</th></tr></thead><tbody>{data.bots.map((bot) => <tr key={bot.user_id}><td><strong>{bot.nickname || "未命名"}</strong></td><td><code>{bot.user_id}</code></td><td>{(bot.confidence * 100).toFixed(0)}%</td><td>{bot.source}</td><td><StateBadge tone={botTone(bot.status)}>{bot.status}</StateBadge></td><td>{bot.evidence_tags.join(" / ") || "—"}</td><td><div className="inline-controls"><button className="button" type="button" disabled={mutation.isPending || bot.status === "approved"} onClick={() => mutation.mutate({ kind: "bot", userId: bot.user_id, nickname: bot.nickname, action: "approve" })}>批准</button><button className="button button-secondary" type="button" disabled={mutation.isPending || bot.status === "rejected"} onClick={() => window.confirm(`确认拒绝 ${bot.user_id}？`) && mutation.mutate({ kind: "bot", userId: bot.user_id, action: "reject" })}>拒绝</button><button className="button button-danger" type="button" disabled={mutation.isPending || !bot.manual_override} onClick={() => window.confirm(`确认清除 ${bot.user_id} 的管理员覆盖并恢复候选状态？`) && mutation.mutate({ kind: "bot", userId: bot.user_id, action: "clear" })}>清除覆盖</button></div></td></tr>)}</tbody></table></div> : <EmptyState code="peer_bot_registry_empty">当前群尚未观察或配置 Peer Bot。</EmptyState>}
        </Panel>

        <Panel eyebrow="PEER BOT / COMMANDS" title={`协议能力目录（${data.commands.length}）`} action={<button className="button button-secondary" type="button" onClick={() => { setDraft(emptyDraft(data.bots[0]?.user_id ?? "")); setArgumentsText("{}"); setDryRunResult(""); }}>新增模板</button>}>
          {data.commands.length ? <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>命令 ID</th><th>用途</th><th>完整模板</th><th>风险</th><th>学习证据</th><th>状态</th><th>操作</th></tr></thead><tbody>{data.commands.map((command) => <tr key={command.command_id}><td><code>{command.command_id}</code><small>{command.target_bot_id}</small></td><td>{command.description || "未填写用途说明"}</td><td><code>{command.full_template}</code><small>{command.legacy_mode ? "legacy 兼容" : `${command.command_entry} / ${command.subcommands.join(" / ") || "无子命令"}`}</small></td><td><StateBadge tone={riskTone(command.risk_level)}>{command.risk_level}</StateBadge></td><td>{command.auto_approved ? "自动批准" : "管理员配置"} · {command.evidence_count} 条</td><td><StateBadge tone={botTone(command.status)}>{command.status}</StateBadge></td><td><div className="inline-controls"><button className="button button-secondary" type="button" onClick={() => { setDraft(commandDraft(command)); setArgumentsText("{}"); setDryRunResult(""); }}>编辑 / Dry-run</button><button className="button button-danger" type="button" disabled={mutation.isPending} onClick={() => window.confirm(`确认删除命令 ${command.command_id}？`) && mutation.mutate({ kind: "delete-command", userId: command.target_bot_id, commandId: command.command_id })}>删除</button></div></td></tr>)}</tbody></table></div> : <EmptyState code="peer_bot_commands_empty">尚未配置完整命令模板。</EmptyState>}

          <div className="peer-bot-editor">
            <h3>模板编辑器</h3>
            <div className="form-grid">
              <div className="member-selector form-span"><label>搜索当前群成员<input type="search" value={memberSearch} onChange={(event) => setMemberSearch(event.target.value)} placeholder="QQ 号、群名片或昵称" /></label><label>目标 Bot<select value={draft.target_bot_id} onChange={(event) => setDraft({ ...draft, target_bot_id: event.target.value })} aria-invalid={validation.field === "target_bot_id"} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"}><option value="">选择当前群成员</option>{memberOptions.map((member) => <option key={String(member.user_id)} value={String(member.user_id)}>{member.card || member.nickname || "未命名成员"}（{String(member.user_id)}）</option>)}</select></label>{membersQuery.isError && <p className="muted-copy">实时成员目录暂不可用；仍可选择注册表中已有的 Bot。</p>}</div>
              <label>命令 ID<input value={draft.command_id} onChange={(event) => setDraft({ ...draft, command_id: event.target.value })} aria-invalid={validation.field === "command_id"} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>
              <label>风险等级<select value={draft.risk_level} onChange={(event) => setDraft({ ...draft, risk_level: event.target.value as PeerBotRiskLevel })}><option value="read">read（只读）</option><option value="write">write（写入）</option><option value="admin">admin（Agent 永不调用）</option><option value="dangerous">dangerous（Agent 永不调用）</option></select></label>
              <label>审核状态<select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as PeerBotStatus })}><option value="candidate">candidate</option><option value="approved">approved</option><option value="rejected">rejected</option></select></label>
              <fieldset className="peer-command-mode form-span"><legend>编辑模式</legend><label className="checkbox-label"><input type="radio" name="peer-command-mode" checked={(draft.mode ?? "legacy") === "structured"} onChange={() => switchEditMode("structured")} />结构化协议 v2</label><label className="checkbox-label"><input type="radio" name="peer-command-mode" checked={(draft.mode ?? "legacy") === "legacy"} onChange={() => switchEditMode("legacy")} />legacy 完整模板</label></fieldset>
              {(draft.mode ?? "legacy") === "structured" ? <><label>命令入口<input value={draft.command_entry ?? ""} onChange={(event) => setDraft({ ...draft, command_entry: event.target.value })} placeholder=".mc 或 /抽卡" aria-invalid={validation.field === "command_entry"} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label><label>一级子命令（可选）<input value={draft.subcommand_1 ?? ""} onChange={(event) => setDraft({ ...draft, subcommand_1: event.target.value })} placeholder="say" /></label><label>二级子命令（可选）<input value={draft.subcommand_2 ?? ""} onChange={(event) => setDraft({ ...draft, subcommand_2: event.target.value })} /></label><label>参数模板（可选）<input value={draft.argument_template ?? ""} onChange={(event) => setDraft({ ...draft, argument_template: event.target.value })} placeholder="{message}" aria-invalid={validation.field === "argument_template"} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label><label className="form-span">用途说明<input value={draft.description ?? ""} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="例如：向 Minecraft 在线玩家发送聊天消息" /></label></> : <label className="form-span">完整命令模板<textarea rows={3} value={draft.full_template} onChange={(event) => setDraft({ ...draft, full_template: event.target.value })} placeholder=".mc say {message} 或 /抽卡" aria-invalid={validation.field === "full_template"} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>}
              <label className="form-span">完整命令预览<output className="peer-full-command-preview">{composePeerBotCommand(draft) || "尚未生成命令"}</output></label>
              <label className="form-span">高级参数 schema<textarea rows={9} value={draft.parameter_schema_text} onChange={(event) => setDraft({ ...draft, parameter_schema_text: event.target.value })} aria-invalid={validation.field === "parameter_schema"} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>
            </div>
            <p id="peer-command-help" className="muted-copy">入口必填，子命令可留空；占位符使用单花括号，例如 <code>{"{message}"}</code>。参数 description 会进入 Agent 能力目录。</p>
            {validation.error && <p id="peer-command-error" className="state-error" role="alert">{validation.error}</p>}
            <div className="peer-bot-dry-run"><label>Dry-run 参数（JSON）<textarea rows={3} value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} /></label><div className="inline-controls"><button className="button button-secondary" type="button" onClick={generateSimpleSchema}>按占位符生成简单参数</button><button className="button button-secondary" type="button" disabled={Boolean(validation.error)} onClick={() => setDryRunResult(renderPeerBotCommandDryRun(draft, validation, argumentsText))}>仅验证，不发送</button><button className="button" type="button" disabled={mutation.isPending || Boolean(validation.error) || !validation.schema} onClick={() => mutation.mutate({ kind: "save-command", draft, schema: validation.schema })}>保存模板</button></div>{dryRunResult && <output className="peer-bot-preview" aria-live="polite">{dryRunResult}</output>}</div>
          </div>
        </Panel>

        <Panel eyebrow="PEER BOT / INVOCATIONS" title="近期调用与关联状态">
          {data.recent_invocations.length ? <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>Tracking ID</th><th>命令 ID</th><th>目标 Bot</th><th>发送</th><th>关联</th><th>回复数</th><th>耗时</th><th>诊断</th></tr></thead><tbody>{data.recent_invocations.map((item) => <tr key={`${item.tracking_id}:${item.status}`}><td><code>{item.tracking_id}</code></td><td><code>{item.command_id}</code></td><td><code>{item.target_bot_id}</code></td><td><StateBadge tone={item.send_status === "sent" ? "ok" : item.send_status === "failed" ? "error" : "unknown"}>{item.send_status}</StateBadge></td><td><StateBadge tone={item.status === "completed" ? "ok" : item.status === "pending" ? "running" : item.status === "timeout" ? "warn" : "error"}>{item.status}</StateBadge></td><td>{item.reply_message_count}</td><td>{item.elapsed_ms} ms</td><td><code>{item.diagnostic_code}</code></td></tr>)}</tbody></table></div> : <EmptyState code="peer_bot_invocations_empty">暂无进程内调用摘要；这里不会显示命令正文或第三方回复原文。</EmptyState>}
          <p className="muted-copy">注册表更新时间：{formatDateTime(data.updated_at)}。运行状态只显示稳定 ID、状态、计数、耗时和诊断码。</p>
        </Panel>
      </>}
    </QueryBoundary>
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </div>;
}
