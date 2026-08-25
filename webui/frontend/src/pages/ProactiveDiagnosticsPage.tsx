import { useQuery } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";

import { resources } from "../api/resources";
import type { ProactiveRecord } from "../api/types";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime } from "../lib/format";

const SCOPES = [
  { value: "", label: "全部" },
  { value: "private", label: "主动私聊" },
  { value: "group", label: "群主动接话" },
  { value: "qzone", label: "QQ 空间" },
];

function outcomeLabel(value: string): string {
  if (value === "sent") return "已发送";
  if (value === "skip_llm_decided") return "Agent 决定跳过";
  if (value === "skip_cooldown") return "冷却中";
  if (value === "skip_probability") return "概率门未通过";
  if (value === "skip_daily_limit") return "达到每日上限";
  if (value === "skip_quiet_hour") return "静默时段";
  if (value === "skip_disabled") return "功能未启用";
  return value || "未分类";
}

function detailSummary(item: ProactiveRecord): string {
  const parts: string[] = [];
  for (const [key, label] of [["action", "动作"], ["len", "长度"], ["since_last_seconds", "距上次"], ["min_interval_minutes", "最短间隔"]] as const) {
    const value = item.detail[key];
    if (typeof value === "string" || typeof value === "number") parts.push(`${label}=${value}`);
  }
  return parts.join(" · ") || "没有可见详情";
}

export function ProactiveDiagnosticsPage() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const scope = params.get("scope") ?? "";
  const outcome = params.get("outcome") ?? "";
  const target = params.get("target") ?? "";
  const cursor = Number(params.get("cursor") ?? 0) || 0;
  const section = location.pathname.endsWith("/overview") ? "overview" : location.pathname.endsWith("/next-eligible") ? "next" : "recent";
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "cursor") next.delete("cursor");
    setParams(next);
  };
  const stats = useQuery({ queryKey: ["proactive", "stats", scope], queryFn: ({ signal }) => resources.proactiveStats(scope, signal), enabled: section === "overview" });
  const recent = useQuery({ queryKey: ["proactive", "recent", scope, outcome, target, cursor], queryFn: ({ signal }) => resources.proactiveRecent({ scope, outcome, target, cursor, limit: 50 }, signal), enabled: section === "recent", placeholderData: (previous) => previous });
  const nextEligible = useQuery({ queryKey: ["proactive", "next", scope], queryFn: ({ signal }) => resources.proactiveNextEligible(scope, signal), enabled: section === "next" });
  const activeQuery = section === "overview" ? stats : section === "next" ? nextEligible : recent;

  return <div className="page-stack">
    <PageHeader index="主动诊断" title="主动行为诊断" description="按结构化结果查看发送、Agent 跳过、冷却和下一可用窗口。记录使用服务端游标读取，不再把接口对象拆成字段路径。" actions={section === "recent" ? <SearchField value={target} onChange={(value) => setFilter("target", value)} placeholder="筛选目标 QQ / 群" /> : undefined} />
    <div className="segmented-control" role="tablist" aria-label="主动行为类型">
      {SCOPES.map((item) => <button key={item.value || "all"} type="button" role="tab" aria-selected={scope === item.value} onClick={() => setFilter("scope", item.value)}>{item.label}</button>)}
    </div>
    {section === "recent" && <Panel eyebrow="FILTER / OUTCOME" title="结果筛选"><select value={outcome} onChange={(event) => setFilter("outcome", event.target.value)} aria-label="按结果筛选"><option value="">全部结果</option><option value="sent">已发送</option><option value="skip_llm_decided">Agent 决定跳过</option><option value="skip_cooldown">冷却中</option><option value="skip_probability">概率门未通过</option><option value="skip_daily_limit">达到每日上限</option></select></Panel>}
    <QueryBoundary isPending={activeQuery.isPending} error={activeQuery.error}>
      {section === "overview" && stats.data && <>
        <div className="metric-rack"><article><span>触发总数</span><strong>{stats.data.total}</strong><small>最近 {stats.data.since_hours} 小时</small></article><article><span>已发送</span><strong>{stats.data.sent}</strong><small>得到发送确认</small></article><article><span>跳过</span><strong>{stats.data.skip}</strong><small>结构化 skip 原因</small></article><article><span>发送率</span><strong>{stats.data.total ? `${Math.round(stats.data.sent / stats.data.total * 100)}%` : "—"}</strong><small>不含未分类结果</small></article></div>
        <Panel eyebrow="STATISTICS / OUTCOME" title="结果分布">{Object.keys(stats.data.counts).length ? <div className="outcome-ledger">{Object.entries(stats.data.counts).map(([key, value]) => <div key={key}><StateBadge tone={key === "sent" ? "ok" : "warn"} raw={key}>{outcomeLabel(key)}</StateBadge><strong>{value}</strong></div>)}</div> : <EmptyState code="proactive_stats_empty">最近 72 小时没有主动触发记录。</EmptyState>}</Panel>
      </>}
      {section === "recent" && recent.data && <>
        {recent.data.items.length ? <Panel eyebrow="EVENTS / CURSOR" title={`最近 ${recent.data.items.length} 条触发记录`}><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>时间</th><th>类型</th><th>结果</th><th>对象</th><th>详情</th><th>下一触发</th></tr></thead><tbody>{recent.data.items.map((item) => <tr key={item.id}><td>{formatDateTime(item.ts)}</td><td><code>{item.scope}</code></td><td><StateBadge tone={item.outcome === "sent" ? "ok" : "warn"} raw={item.outcome}>{outcomeLabel(item.outcome)}</StateBadge></td><td><code>{item.target || "—"}</code></td><td className="wrap-cell">{detailSummary(item)}</td><td>{formatDateTime(item.next_eligible_at)}</td></tr>)}</tbody></table></div></Panel> : <EmptyState code="proactive_recent_empty">当前筛选条件下没有主动触发记录。</EmptyState>}
        <div className="pagination"><button type="button" disabled={!cursor} onClick={() => setFilter("cursor", "")}>回到最新</button><span>{cursor ? `游标 ${cursor}` : "最新记录"}</span><button type="button" disabled={!recent.data.has_more || !recent.data.next_cursor} onClick={() => setFilter("cursor", String(recent.data.next_cursor))}>较早记录</button></div>
      </>}
      {section === "next" && nextEligible.data && (nextEligible.data.items.length ? <Panel eyebrow="SCHEDULE / NEXT" title="下一可用窗口"><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>类型</th><th>目标</th><th>最近记录</th><th>下一可用时间</th></tr></thead><tbody>{nextEligible.data.items.map((item) => <tr key={`${item.scope}:${item.target}`}><td><code>{item.scope}</code></td><td><code>{item.target}</code></td><td>{formatDateTime(item.latest_ts)}</td><td>{formatDateTime(item.next_eligible_at)}</td></tr>)}</tbody></table></div></Panel> : <EmptyState code="proactive_next_empty">当前没有带下一可用时间的记录。</EmptyState>)}
    </QueryBoundary>
  </div>;
}
