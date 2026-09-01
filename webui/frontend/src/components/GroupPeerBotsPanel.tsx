import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { diagnosticFromError, safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type {
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
  target_bot_id: string;
  command_id: string;
  full_template: string;
  parameter_schema_text: string;
  risk_level: PeerBotRiskLevel;
  status: PeerBotStatus;
};

type LocalValidation = {
  error: string;
  placeholders: string[];
  schema: PeerBotCommandTemplate["parameter_schema"] | null;
};

const EMPTY_SCHEMA = JSON.stringify({
  type: "object",
  properties: {},
  required: [],
  additionalProperties: false,
}, null, 2);

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
    return { error: "请选择有效的目标 Bot。", placeholders: [], schema: null };
  }
  if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(draft.command_id)) {
    return { error: "命令 ID 只能使用字母、数字、点、冒号、下划线或连字符。", placeholders: [], schema: null };
  }
  const template = draft.full_template.trim();
  if (!template || /[\r\n\u0000-\u001f\u007f-\u009f]/.test(template)) {
    return { error: "完整命令模板必须是单行且不能包含控制字符。", placeholders: [], schema: null };
  }
  if (template.length > Math.max(1, Math.min(4000, maxCommandChars))) {
    return { error: `完整命令模板不能超过 ${maxCommandChars} 个字符。`, placeholders: [], schema: null };
  }
  const parsedTemplate = templateFields(template);
  if (parsedTemplate.error) return { error: parsedTemplate.error, placeholders: [], schema: null };
  const commandHead = literalCommandHead(template);
  if (!commandHead) return { error: "模板必须以固定命令前缀开头，不能以参数占位符开头。", placeholders: [], schema: null };
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
      placeholders: parsedTemplate.fields,
      schema: parsed as PeerBotCommandTemplate["parameter_schema"],
    };
  } catch (error) {
    return {
      error: `参数 schema 无效：${error instanceof Error ? error.message : "无法解析"}`,
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
  const template = draft.full_template.trim();
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
    target_bot_id: targetBotId,
    command_id: "",
    full_template: "",
    parameter_schema_text: EMPTY_SCHEMA,
    risk_level: "read",
    status: "candidate",
  };
}

function commandDraft(command: PeerBotCommandTemplate): CommandDraft {
  return {
    target_bot_id: command.target_bot_id,
    command_id: command.command_id,
    full_template: command.full_template,
    parameter_schema_text: JSON.stringify(command.parameter_schema, null, 2),
    risk_level: command.risk_level,
    status: command.status,
  };
}

export function GroupPeerBotsPanel({ groupId }: { groupId: string }) {
  const history = useDiagnosticHistory(`group-peer-bots-${groupId}`);
  const query = useQuery({
    queryKey: ["group-peer-bots", groupId],
    queryFn: ({ signal }) => resources.groupPeerBots(groupId, signal),
    enabled: Boolean(groupId),
  });
  const data = query.data;
  const [enabled, setEnabled] = useState(false);
  const [cooldown, setCooldown] = useState("10");
  const [ttl, setTtl] = useState("30");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [draft, setDraft] = useState<CommandDraft>(() => emptyDraft());
  const [argumentsText, setArgumentsText] = useState("{}");
  const [dryRunResult, setDryRunResult] = useState("");

  useEffect(() => {
    if (!data) return;
    if (!settingsDirty) {
      setEnabled(data.enabled);
      setCooldown(String(data.policies.cooldown_seconds));
      setTtl(String(data.policies.pending_ttl_seconds));
    }
    setDraft((current) => current.target_bot_id ? current : emptyDraft(data.bots[0]?.user_id ?? ""));
  }, [data, settingsDirty]);

  useEffect(() => setSettingsDirty(false), [groupId]);

  const validation = useMemo(() => validatePeerBotCommandDraft(draft, data?.max_command_chars ?? 500), [data?.max_command_chars, draft]);
  const mutation = useMutation({
    mutationFn: async (action: { kind: string; [key: string]: unknown }) => {
      if (action.kind === "settings") return resources.updateGroupPeerBotSettings(groupId, action.body as { enabled: boolean; max_calls_per_turn: 1; cooldown_seconds: number; pending_ttl_seconds: number; max_chain_depth: 1 });
      if (action.kind === "bot") return resources.updateGroupPeerBotStatus(groupId, String(action.userId), action.action as "approve" | "reject" | "clear", String(action.nickname ?? ""));
      if (action.kind === "discover") return resources.discoverGroupPeerBots(groupId);
      if (action.kind === "reset") return resources.resetGroupPeerBotLoop(groupId);
      if (action.kind === "delete-command") return resources.deleteGroupPeerBotCommand(groupId, String(action.userId), String(action.commandId));
      const saveDraft = action.draft as CommandDraft;
      const saveSchema = action.schema as PeerBotCommandTemplate["parameter_schema"];
      return resources.saveGroupPeerBotCommand(groupId, saveDraft.target_bot_id, saveDraft.command_id, {
        full_template: saveDraft.full_template.trim(),
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
    mutation.mutate({ kind: "settings", body: { enabled, max_calls_per_turn: 1, cooldown_seconds: cooldownValue, pending_ttl_seconds: ttlValue, max_chain_depth: 1 } });
  };

  return <div className="page-stack peer-bot-page">
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {data && <>
        <div className="summary-grid">
          <Panel eyebrow="PEER BOT / POLICY" title="协作与循环保护">
            <div className="form-grid peer-bot-settings-grid">
              <label className="checkbox-label"><input type="checkbox" checked={enabled} onChange={(event) => { setEnabled(event.target.checked); setSettingsDirty(true); }} />启用本群外部 Bot 调用</label>
              <label>冷却秒数<input type="number" min="0" max="3600" value={cooldown} onChange={(event) => { setCooldown(event.target.value); setSettingsDirty(true); }} /></label>
              <label>回复等待 TTL<input type="number" min="1" max="600" value={ttl} onChange={(event) => { setTtl(event.target.value); setSettingsDirty(true); }} /></label>
            </div>
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

        <Panel eyebrow="PEER BOT / COMMANDS" title={`完整命令模板（${data.commands.length}）`} action={<button className="button button-secondary" type="button" onClick={() => { setDraft(emptyDraft(data.bots[0]?.user_id ?? "")); setArgumentsText("{}"); setDryRunResult(""); }}>新增模板</button>}>
          {data.commands.length ? <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>命令 ID</th><th>目标 Bot</th><th>完整模板</th><th>风险</th><th>状态</th><th>版本</th><th>操作</th></tr></thead><tbody>{data.commands.map((command) => <tr key={command.command_id}><td><code>{command.command_id}</code></td><td><code>{command.target_bot_id}</code></td><td><code>{command.full_template}</code></td><td><StateBadge tone={riskTone(command.risk_level)}>{command.risk_level}</StateBadge></td><td><StateBadge tone={botTone(command.status)}>{command.status}</StateBadge></td><td>v{command.version}</td><td><div className="inline-controls"><button className="button button-secondary" type="button" onClick={() => { setDraft(commandDraft(command)); setArgumentsText("{}"); setDryRunResult(""); }}>编辑 / Dry-run</button><button className="button button-danger" type="button" disabled={mutation.isPending} onClick={() => window.confirm(`确认删除命令 ${command.command_id}？`) && mutation.mutate({ kind: "delete-command", userId: command.target_bot_id, commandId: command.command_id })}>删除</button></div></td></tr>)}</tbody></table></div> : <EmptyState code="peer_bot_commands_empty">尚未配置完整命令模板。</EmptyState>}

          <div className="peer-bot-editor">
            <h3>模板编辑器</h3>
            <div className="form-grid">
              <label>目标 Bot ID<input value={draft.target_bot_id} onChange={(event) => setDraft({ ...draft, target_bot_id: event.target.value })} list="peer-bot-id-options" aria-invalid={validation.error.startsWith("请选择有效")} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>
              <datalist id="peer-bot-id-options">{data.bots.map((bot) => <option key={bot.user_id} value={bot.user_id}>{bot.nickname}</option>)}</datalist>
              <label>命令 ID<input value={draft.command_id} onChange={(event) => setDraft({ ...draft, command_id: event.target.value })} aria-invalid={validation.error.startsWith("命令 ID")} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>
              <label>风险等级<select value={draft.risk_level} onChange={(event) => setDraft({ ...draft, risk_level: event.target.value as PeerBotRiskLevel })}><option value="read">read（只读）</option><option value="write">write（写入）</option><option value="admin">admin（Agent 永不调用）</option><option value="dangerous">dangerous（Agent 永不调用）</option></select></label>
              <label>审核状态<select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as PeerBotStatus })}><option value="candidate">candidate</option><option value="approved">approved</option><option value="rejected">rejected</option></select></label>
              <label className="form-span">完整命令模板<textarea rows={3} value={draft.full_template} onChange={(event) => setDraft({ ...draft, full_template: event.target.value })} placeholder=".mc say {message} 或 /抽卡" aria-invalid={Boolean(validation.error && !validation.error.startsWith("参数 schema") && !validation.error.startsWith("请选择有效") && !validation.error.startsWith("命令 ID"))} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>
              <label className="form-span">参数 schema<textarea rows={9} value={draft.parameter_schema_text} onChange={(event) => setDraft({ ...draft, parameter_schema_text: event.target.value })} aria-invalid={validation.error.startsWith("参数 schema")} aria-describedby={validation.error ? "peer-command-help peer-command-error" : "peer-command-help"} /></label>
            </div>
            <p id="peer-command-help" className="muted-copy">占位符使用单花括号，例如 <code>{"{message}"}</code>；properties 必须与占位符完全一致，且禁止额外参数。</p>
            {validation.error && <p id="peer-command-error" className="state-error" role="alert">{validation.error}</p>}
            <div className="peer-bot-dry-run"><label>Dry-run 参数（JSON）<textarea rows={3} value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} /></label><div className="inline-controls"><button className="button button-secondary" type="button" disabled={Boolean(validation.error)} onClick={() => setDryRunResult(renderPeerBotCommandDryRun(draft, validation, argumentsText))}>仅验证，不发送</button><button className="button" type="button" disabled={mutation.isPending || Boolean(validation.error) || !validation.schema} onClick={() => mutation.mutate({ kind: "save-command", draft, schema: validation.schema })}>保存模板</button></div>{dryRunResult && <output className="peer-bot-preview" aria-live="polite">{dryRunResult}</output>}</div>
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
