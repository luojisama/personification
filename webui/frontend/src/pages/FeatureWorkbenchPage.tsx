import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { resources } from "../api/resources";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";

export type FeatureId = "proactive" | "group-switches" | "memory-palace" | "persona-preview" | "persona-builder" | "plugins" | "user-policies" | "outbound" | "data-transfer" | "audit" | "qq" | "devices";
interface FeatureSpec { index: string; title: string; description: string; sources: Array<{ label: string; path: string; v2?: boolean }>; note?: string }

const SPECS: Record<FeatureId, FeatureSpec> = {
  proactive: { index: "07", title: "主动诊断", description: "核对主动消息运行窗口、阻止原因、调度状态和最近结果。", sources: [{ label: "主动统计", path: "/proactive/stats" }, { label: "最近调度", path: "/proactive/recent" }, { label: "下一可用窗口", path: "/proactive/next-eligible" }] },
  "group-switches": { index: "13", title: "群开关", description: "读取群白名单和运行时启用状态；修改操作仍受管理员、CSRF 与后端热加载约束。", sources: [{ label: "群白名单", path: "/groups/whitelist" }] },
  "memory-palace": { index: "15", title: "记忆宫殿", description: "查看记忆层级、关联关系、宫殿分区和可追踪来源。", sources: [{ label: "记忆图谱", path: "/memory/graph" }, { label: "宫殿分区", path: "/memory/palace-zones" }, { label: "向量索引", path: "/memory/vector-index" }] },
  "persona-preview": { index: "17", title: "人设预览", description: "使用当前服务端结构化 Prompt 生成器预览实际人设上下文和质量告警。", sources: [{ label: "Prompt 预览", path: "/test/persona-prompt" }] },
  "persona-builder": { index: "18", title: "人设构建", description: "查看构建历史、候选状态和验证结果；构建与应用操作继续走现有审计接口。", sources: [{ label: "构建历史", path: "/persona-template/history" }] },
  plugins: { index: "23", title: "插件管理", description: "查看版本、能力、启停状态、更新历史和稳定诊断码。", sources: [{ label: "插件状态", path: "/plugin-manager/status" }, { label: "更新历史", path: "/plugin-manager/history" }] },
  "user-policies": { index: "25", title: "用户策略与黑名单", description: "分页查看策略范围、来源、有效状态与自动门控结果。", sources: [{ label: "策略状态", path: "/user-policy/states" }] },
  outbound: { index: "26", title: "近期 Bot 消息", description: "查看发送结果、Operation ID、Trace 关联和可用的撤回证据。", sources: [{ label: "发送记录", path: "/outbound/recent" }] },
  "data-transfer": { index: "27", title: "数据迁移", description: "保留旧状态迁移，并展示完整备份、dry-run、apply、rollback 和二次验证能力。", sources: [{ label: "迁移与备份能力", path: "/settings", v2: true }], note: "秘密包导入导出与全量 apply 在公网 HTTP 下会被服务端拒绝；当前入口只展示能力和安全边界。" },
  audit: { index: "28", title: "审计日志", description: "查看管理员操作、结果与脱敏详情。", sources: [{ label: "审计记录", path: "/audit/recent" }, { label: "操作分类", path: "/audit/actions" }] },
  qq: { index: "30", title: "QQ 管理", description: "查看账号、群和好友状态；危险修改操作需要在专用确认表单中完成。", sources: [{ label: "账号信息", path: "/qq/info" }, { label: "群列表", path: "/qq/groups" }, { label: "好友列表", path: "/qq/friends" }] },
  devices: { index: "31", title: "设备管理", description: "查看已授权、待审批和信任设备，核对当前管理员登录边界。", sources: [{ label: "授权设备", path: "/auth/devices" }, { label: "待审批设备", path: "/auth/pending-devices" }, { label: "信任设备", path: "/auth/trusted-devices" }] },
};

function flatten(value: unknown, prefix = "", depth = 0): Array<{ key: string; value: string }> {
  if (depth > 3) return [{ key: prefix || "value", value: "[结构过深，已折叠]" }];
  if (value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return [{ key: prefix || "value", value: String(value ?? "—") }];
  if (Array.isArray(value)) return value.slice(0, 100).flatMap((item, index) => flatten(item, `${prefix}[${index}]`, depth + 1));
  if (typeof value === "object") return Object.entries(value as Record<string, unknown>).flatMap(([key, item]) => flatten(item, prefix ? `${prefix}.${key}` : key, depth + 1));
  return [];
}

export function FeatureWorkbenchPage({ feature }: { feature: FeatureId }) {
  const spec = SPECS[feature];
  const [search, setSearch] = useState("");
  const query = useQuery({ queryKey: ["feature-workbench", feature], queryFn: async ({ signal }) => Promise.all(spec.sources.map(async (source) => ({ ...source, data: source.v2 ? await resources.runtimeSettings(signal) : await resources.legacy(source.path, undefined, signal) }))) });
  return <div className="page-stack">
    <PageHeader index={spec.index} title={spec.title} description={spec.description} actions={<SearchField value={search} onChange={setSearch} placeholder="筛选字段和值" />} />
    {spec.note && <div className="security-manifest">{spec.note}</div>}
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {query.data?.map((source) => <FeatureSource key={source.path} label={source.label} path={source.path} data={source.data} search={search} />)}
    </QueryBoundary>
  </div>;
}

function FeatureSource({ label, path, data, search }: { label: string; path: string; data: unknown; search: string }) {
  const rows = useMemo(() => { const needle = search.trim().toLocaleLowerCase("zh-CN"); return flatten(data).filter((row) => !needle || `${row.key} ${row.value}`.toLocaleLowerCase("zh-CN").includes(needle)); }, [data, search]);
  return <Panel eyebrow={`SERVICE / ${path}`} title={label} action={<StateBadge tone="ok">服务已连接</StateBadge>}>
    {rows.length ? <div className="trace-table-wrap"><table className="forensic-table key-value-table"><thead><tr><th>字段</th><th>值</th></tr></thead><tbody>{rows.slice(0, 500).map((row, index) => <tr key={`${row.key}:${index}`}><td><code>{row.key}</code></td><td className="wrap-cell">{row.value}</td></tr>)}</tbody></table></div> : <EmptyState code="feature_data_empty">当前接口没有记录，或筛选条件没有命中。</EmptyState>}
  </Panel>;
}
