import { useQuery } from "@tanstack/react-query";

import { resources } from "../api/resources";
import { useBot } from "../app/BotContext";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime } from "../lib/format";

const ACTIONS: Record<string, string> = { login_state: "登录态", own_feed_read: "读取自己的动态", friend_feed_read: "读取朋友动态", publish: "发布", like: "点赞", forward: "转发", top_level_comment: "顶级评论", child_comment_reply: "子评论回复" };

export function QzoneCapabilitiesPage() {
  const { botId } = useBot();
  const query = useQuery({ queryKey: ["qzone-capabilities", botId], queryFn: ({ signal }) => resources.qzoneCapabilities(signal, botId) });
  const rows = Array.isArray(query.data?.items) ? query.data.items.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null) : [];
  return <div className="page-stack">
    <PageHeader index="10" title="QQ 空间" description="按具体操作展示登录态、接口、HTTP/业务码、字段缺失和认证状态。未知不等于不支持，写操作结果未知时不会自动重试。" />
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {rows.length ? <Panel eyebrow="QZONE / CAPABILITY MATRIX" title="生产能力矩阵">
        <div className="qzone-capability-grid">{rows.map((row) => { const state = String(row.state || "unknown"); return <article key={String(row.action)}><header><strong>{ACTIONS[String(row.action)] || String(row.action)}</strong><StateBadge tone={state === "available" ? "ok" : state === "degraded" ? "warn" : state === "unavailable" ? "error" : "unknown"} raw={state}>{state}</StateBadge></header><dl><div><dt>接口</dt><dd><code>{String(row.interface || "未观测")}</code></dd></div><div><dt>HTTP / 业务码</dt><dd>{String(row.http_status ?? "—")} / <code>{String(row.business_code || "—")}</code></dd></div><div><dt>认证状态</dt><dd>{String(row.auth_state || "unknown")}</dd></div><div><dt>诊断码</dt><dd><code>{String(row.detail_code || "unknown")}</code></dd></div><div><dt>缺失字段</dt><dd>{Array.isArray(row.missing_fields) ? row.missing_fields.join("、") || "无" : "—"}</dd></div><div><dt>最后验证</dt><dd>{formatDateTime(row.checked_at as string | number | null)}</dd></div></dl></article>; })}</div>
      </Panel> : <EmptyState code="qzone_capabilities_empty">尚无 QQ 空间能力记录。</EmptyState>}
    </QueryBoundary>
  </div>;
}
