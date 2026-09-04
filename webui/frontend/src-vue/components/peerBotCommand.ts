import type {
  PeerBotCommandTemplate,
  PeerBotRiskLevel,
  PeerBotStatus,
} from "@/api/types";

export type CommandDraft = {
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

export type LocalValidation = {
  error: string;
  field: "target_bot_id" | "command_id" | "command_entry" | "argument_template" | "full_template" | "parameter_schema" | null;
  placeholders: string[];
  schema: PeerBotCommandTemplate["parameter_schema"] | null;
};

export const EMPTY_SCHEMA = JSON.stringify({
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
export function templateFields(template: string): { fields: string[]; error: string } {
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

export function literalCommandHead(template: string): string {
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

export function valueMatchesPeerBotType(value: unknown, type: string): boolean {
  if (type === "string") return typeof value === "string";
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "boolean") return typeof value === "boolean";
  return false;
}

export function botTone(status: PeerBotStatus): "ok" | "warn" | "error" {
  return status === "approved" ? "ok" : status === "rejected" ? "error" : "warn";
}

export function riskTone(risk: PeerBotRiskLevel): "ok" | "warn" | "error" | "running" {
  return risk === "read" ? "ok" : risk === "write" ? "running" : risk === "admin" ? "warn" : "error";
}

export function emptyDraft(targetBotId = ""): CommandDraft {
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

export function commandDraft(command: PeerBotCommandTemplate): CommandDraft {
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

export function structuredFieldsFromLegacy(fullTemplate: string): Pick<CommandDraft, "command_entry" | "subcommand_1" | "subcommand_2" | "argument_template"> {
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
