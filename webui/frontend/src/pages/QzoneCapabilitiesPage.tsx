import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { diagnosticFromError, safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import { API_BASE } from "../api/client";
import { useBot } from "../app/BotContext";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime } from "../lib/format";

type JsonRecord = Record<string, unknown>;
const ACTIONS: Record<string, string> = { login_state: "登录态", own_feed_read: "读取自己的动态", friend_feed_read: "读取朋友动态", publish: "发布", like: "点赞", forward: "转发", top_level_comment: "顶级评论", child_comment_reply: "子评论回复" };
const SECURE_TRANSPORT = window.location.protocol === "https:" || ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

function record(value: unknown): JsonRecord { return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {}; }
function records(value: unknown): JsonRecord[] { return Array.isArray(value) ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : []; }
function value(value: unknown, fallback = "—") { return value === null || value === undefined || value === "" ? fallback : String(value); }

export function QzoneCapabilitiesPage() {
  const { section = "capabilities" } = useParams();
  return <div className="page-stack">
    <PageHeader index={`QQ 空间 / ${section === "capabilities" ? "能力矩阵" : section === "auth" ? "登录与恢复" : section === "feeds" ? "只读动态" : section === "operations" ? "写操作" : "操作历史"}`} title="QQ 空间" description="只读、登录恢复、外部写和结果核对分区处理；任何结果未知的写操作都不会自动重试。" />
    {section === "capabilities" ? <CapabilityMatrix /> : section === "auth" ? <QzoneAuth /> : section === "feeds" ? <QzoneFeeds /> : section === "operations" ? <QzoneOperations /> : <QzoneHistory />}
  </div>;
}

function CapabilityMatrix() {
  const { botId } = useBot();
  const query = useQuery({ queryKey: ["qzone-capabilities", botId], queryFn: ({ signal }) => resources.qzoneCapabilities(signal, botId) });
  const rows = records(query.data?.items);
  return <QueryBoundary isPending={query.isPending} error={query.error}>{rows.length ? <Panel eyebrow="QZONE / CAPABILITY MATRIX" title="生产能力矩阵"><div className="qzone-capability-grid">{rows.map((row) => { const state = String(row.state || "unknown"); return <article key={String(row.action)}><header><strong>{ACTIONS[String(row.action)] || String(row.action)}</strong><StateBadge tone={state === "available" ? "ok" : state === "degraded" ? "warn" : state === "unavailable" ? "error" : "unknown"}>{state}</StateBadge></header><dl><div><dt>接口</dt><dd><code>{value(row.interface, "未观测")}</code></dd></div><div><dt>HTTP / 业务码</dt><dd>{value(row.http_status)} / <code>{value(row.business_code)}</code></dd></div><div><dt>认证状态</dt><dd>{value(row.auth_state, "unknown")}</dd></div><div><dt>诊断码</dt><dd><code>{value(row.detail_code, "unknown")}</code></dd></div><div><dt>缺失字段</dt><dd>{Array.isArray(row.missing_fields) ? row.missing_fields.join("、") || "无" : "—"}</dd></div><div><dt>最后验证</dt><dd>{formatDateTime(row.checked_at as string | number | null)}</dd></div></dl></article>; })}</div></Panel> : <EmptyState code="qzone_capabilities_empty">尚无 QQ 空间能力记录。</EmptyState>}</QueryBoundary>;
}

function QzoneStatusPanel({ status }: { status: JsonRecord }) {
  const auth = record(status.auth);
  const quota = record(status.quota);
  const reconciliation = record(status.reconciliation);
  return <div className="summary-grid"><Panel eyebrow="AUTH / RUNTIME" title="登录与运行态"><dl className="compact-kv"><dt>总开关</dt><dd>{status.enabled ? "启用" : "停用"}</dd><dt>Cookie</dt><dd>{status.cookie_configured ? "已配置（不回传原值）" : "未配置"}</dd><dt>认证状态</dt><dd>{value(auth.state ?? auth.status, "unknown")}</dd><dt>只读模式</dt><dd>{status.read_only ? "是" : "否"}</dd></dl></Panel><Panel eyebrow="QUOTA / RECONCILIATION" title="额度与结果核对"><dl className="compact-kv"><dt>本月</dt><dd>{value(quota.used ?? quota.count)} / {value(quota.limit)}</dd><dt>下一可用</dt><dd>{formatDateTime(status.next_eligible_at as number)}</dd><dt>对账状态</dt><dd>{value(reconciliation.state, "clear")}</dd><dt>阻塞写操作</dt><dd>{reconciliation.blocking ? "有，禁止重发" : "无"}</dd></dl></Panel></div>;
}

function QzoneAuth() {
  const { botId } = useBot();
  const [sessionId, setSessionId] = useState("");
  const [cookie, setCookie] = useState("");
  const history = useDiagnosticHistory("qzone-auth");
  const status = useQuery({ queryKey: ["qzone-status", botId], queryFn: ({ signal }) => resources.qzoneGet("status", {}, signal) });
  const loginStatus = useQuery({ queryKey: ["qzone-login", sessionId], queryFn: ({ signal }) => resources.qzoneGet(`auth/login/${encodeURIComponent(sessionId)}/status`, {}, signal), enabled: Boolean(sessionId), refetchInterval: sessionId ? 3000 : false });
  const run = useMutation({
    mutationFn: async (action: "start" | "cancel" | "refresh" | "cookie") => action === "start" ? resources.qzonePost("auth/login/start", { bot_id: botId }) : action === "cancel" ? resources.qzonePost(`auth/login/${encodeURIComponent(sessionId)}/cancel`) : action === "refresh" ? resources.qzonePost("refresh-cookie", { bot_id: botId }) : resources.qzonePost("auth/cookie", { bot_id: botId, cookie }),
    onSuccess: (result) => { const diagnostic = record(result.diagnostic); history.record(safeDiagnostic(diagnostic)); const nextSession = String(result.session_id ?? ""); if (nextSession) setSessionId(nextSession); setCookie(""); void status.refetch(); },
    onError: (error) => history.record(diagnosticFromError(error)),
  });
  return <>
    <QueryBoundary isPending={status.isPending} error={status.error}>{status.data && <QzoneStatusPanel status={status.data} />}</QueryBoundary>
    <Panel eyebrow="AUTH / LOGIN RECOVERY" title="扫码恢复"><div className="inline-controls"><button className="button" type="button" disabled={!botId || run.isPending} onClick={() => run.mutate("start")}>创建扫码会话</button><button className="button button-secondary" type="button" disabled={!botId || run.isPending} onClick={() => run.mutate("refresh")}>从协议端刷新 Cookie</button>{sessionId && <button className="button button-danger" type="button" disabled={run.isPending} onClick={() => run.mutate("cancel")}>取消会话</button>}</div>{sessionId && <div className="qzone-login-session"><img src={`${API_BASE}/qzone-management/auth/login/${encodeURIComponent(sessionId)}/qrcode`} alt="QZone 登录二维码" referrerPolicy="no-referrer" /><dl className="compact-kv"><dt>Session ID</dt><dd><code>{sessionId}</code></dd><dt>状态</dt><dd>{value(loginStatus.data?.status, "等待扫码")}</dd><dt>过期</dt><dd>{formatDateTime(loginStatus.data?.expires_at as number)}</dd></dl></div>}</Panel>
    <Panel eyebrow="AUTH / MANUAL COOKIE" title="手工安装 Cookie"><p className="muted-copy">秘密只在本次请求中提交，接口不会回显或写入审计详情。{SECURE_TRANSPORT ? "当前连接允许提交。" : "当前是远程 HTTP，前后端均会拒绝提交；请先部署 HTTPS。"}</p><textarea value={cookie} onChange={(event) => setCookie(event.target.value)} rows={4} autoComplete="off" spellCheck={false} placeholder="粘贴目标 Bot 的 QZone Cookie" disabled={!SECURE_TRANSPORT} /><button className="button" type="button" disabled={!SECURE_TRANSPORT || !botId || !cookie || run.isPending} onClick={() => window.confirm(`确认将 Cookie 安装到 Bot ${botId}？`) && run.mutate("cookie")}>验证并安装</button></Panel>
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </>;
}

function QzoneFeeds() {
  const { botId } = useBot();
  const history = useDiagnosticHistory("qzone-feeds");
  const status = useQuery({ queryKey: ["qzone-status", botId], queryFn: ({ signal }) => resources.qzoneGet("status", {}, signal) });
  const candidates = useQuery({ queryKey: ["qzone-candidates", botId], queryFn: ({ signal }) => resources.qzoneGet("reconcile-candidates", { bot_id: botId }, signal), enabled: false });
  const scan = useMutation({ mutationFn: (kind: "social" | "inbound") => resources.qzonePost("scan-now", { kind, bot_id: botId }), onSuccess: (result) => { history.record(safeDiagnostic(record(result.diagnostic))); void status.refetch(); }, onError: (error) => history.record(diagnosticFromError(error)) });
  const rows = records(candidates.data?.candidates);
  return <>
    <QueryBoundary isPending={status.isPending} error={status.error}>{status.data && <QzoneStatusPanel status={status.data} />}</QueryBoundary>
    <Panel eyebrow="EXTERNAL READ / CONFIRM" title="授权范围内读取"><p>下列操作会访问 QQ 空间公开或已授权数据并可能消耗网络请求，不会执行点赞、评论或发布。</p><div className="inline-controls"><button className="button" type="button" disabled={!botId || candidates.isFetching} onClick={() => window.confirm(`确认读取 Bot ${botId} 的本人动态，用于生成对账候选？`) && void candidates.refetch()}>读取本人动态</button><button className="button button-secondary" type="button" disabled={!botId || scan.isPending} onClick={() => window.confirm(`确认执行 Bot ${botId} 的有限好友动态扫描？`) && scan.mutate("social")}>扫描朋友动态</button><button className="button button-secondary" type="button" disabled={!botId || scan.isPending} onClick={() => window.confirm(`确认执行 Bot ${botId} 的留言轮询？`) && scan.mutate("inbound")}>轮询留言</button></div></Panel>
    {candidates.isError ? <DiagnosticPanel diagnostic={diagnosticFromError(candidates.error)} defaultOpen /> : rows.length ? <Panel eyebrow="READ / OWN FEEDS" title={`本人动态候选（${rows.length}）`}><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>时间</th><th>Feed ID</th><th>安全摘要</th></tr></thead><tbody>{rows.map((item) => <tr key={value(item.feed_id)}><td>{formatDateTime(item.created_at as number)}</td><td><code>{value(item.feed_id)}</code></td><td>{value(item.content)}</td></tr>)}</tbody></table></div></Panel> : candidates.isFetched && <EmptyState code="qzone_feeds_empty">本次读取没有返回可核对动态。</EmptyState>}
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </>;
}

function QzoneOperations() {
  const { botId } = useBot();
  const [confirmation, setConfirmation] = useState("");
  const [operationId, setOperationId] = useState(() => globalThis.crypto?.randomUUID?.() ?? `qzone-${Date.now()}`);
  const history = useDiagnosticHistory("qzone-operations");
  const run = useMutation({ mutationFn: () => resources.qzonePost("post-now", { bot_id: botId, operation_id: operationId }), onSuccess: (result) => { history.record(safeDiagnostic(record(result.diagnostic))); setOperationId(globalThis.crypto?.randomUUID?.() ?? `qzone-${Date.now()}`); setConfirmation(""); }, onError: (error) => history.record(diagnosticFromError(error)) });
  return <>
    <Panel eyebrow="EXTERNAL WRITE / HIGH RISK" title="发布一条 Agent 生成动态"><p>该操作会绕过额度、间隔和 Agent 参与决策，但仍计入月度额度。发布结果未知时 Operation ID 会保持隔离，页面不会自动重试。{SECURE_TRANSPORT ? "" : " 当前是远程 HTTP，必须先部署 HTTPS。"}</p><dl className="compact-kv"><dt>目标 Bot</dt><dd><code>{botId || "未选择"}</code></dd><dt>Operation ID</dt><dd><code>{operationId}</code></dd><dt>动作</dt><dd>生成并发布一条 QZone 说说</dd></dl><label>输入目标 Bot QQ 以确认<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} disabled={!SECURE_TRANSPORT} /></label><button className="button button-danger" type="button" disabled={!SECURE_TRANSPORT || !botId || confirmation !== botId || run.isPending} onClick={() => window.confirm(`最后确认：使用 Bot ${botId} 发布一条真实 QZone 动态？`) && run.mutate()}>确认真实发布</button></Panel>
    <Panel eyebrow="SUPPORTED ACTIONS" title="其他写操作"><p className="muted-copy">点赞、评论、子评论回复和转发必须从具体动态详情携带真实 feed、topic、父评论和目标 UIN；当前未选择完整目标，因此保持禁用，不构造猜测字段。</p><div className="inline-controls"><button type="button" disabled>点赞</button><button type="button" disabled>顶级评论</button><button type="button" disabled>子评论回复</button><button type="button" disabled>转发</button></div></Panel>
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </>;
}

function QzoneHistory() {
  const { botId } = useBot();
  const [operationId, setOperationId] = useState("");
  const history = useDiagnosticHistory("qzone-history");
  const status = useQuery({ queryKey: ["qzone-status", botId], queryFn: ({ signal }) => resources.qzoneGet("status", {}, signal) });
  const operation = useQuery({ queryKey: ["qzone-operation", operationId], queryFn: ({ signal }) => resources.qzoneGet(`operations/${encodeURIComponent(operationId)}`, {}, signal), enabled: Boolean(operationId) });
  const run = useMutation({ mutationFn: (action: "reconcile" | "absent") => resources.qzonePost(`operations/${encodeURIComponent(operationId)}/${action === "reconcile" ? "reconcile" : "resolve-absent"}`, { bot_id: botId }), onSuccess: (result) => { history.record(safeDiagnostic(record(result.diagnostic))); void status.refetch(); void operation.refetch(); }, onError: (error) => history.record(diagnosticFromError(error)) });
  const unresolved = records(record(status.data?.reconciliation).operations);
  return <>
    <Panel eyebrow="OPERATIONS / UNKNOWN RESULTS" title="待人工核对"><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>Operation ID</th><th>状态</th><th>创建时间</th><th>远端 ID</th><th>安全摘要</th></tr></thead><tbody>{unresolved.map((item) => <tr key={value(item.operation_id)} onClick={() => setOperationId(value(item.operation_id, ""))}><td><code>{value(item.operation_id)}</code></td><td><StateBadge tone={item.status === "unknown" ? "unknown" : "warn"}>{value(item.status)}</StateBadge></td><td>{formatDateTime(item.created_at as number)}</td><td>{value(item.remote_id)}</td><td>{value(item.content)}</td></tr>)}</tbody></table></div>{!unresolved.length && !status.isPending && <EmptyState code="qzone_reconciliation_clear">没有阻塞中的未知或发送中操作。</EmptyState>}</Panel>
    <Panel eyebrow="VERIFY / SINGLE OPERATION" title="单个结果核对"><input value={operationId} onChange={(event) => setOperationId(event.target.value)} placeholder="Operation ID" />{operation.data && <dl className="compact-kv"><dt>状态</dt><dd>{value(record(operation.data.operation).status)}</dd><dt>结果码</dt><dd><code>{value(record(operation.data.operation).result_code)}</code></dd><dt>远端 ID</dt><dd>{value(record(operation.data.operation).remote_id)}</dd></dl>}<div className="inline-controls"><button className="button" type="button" disabled={!operationId || !botId || run.isPending} onClick={() => run.mutate("reconcile")}>从本人动态对账</button><button className="button button-danger" type="button" disabled={!operationId || !botId || run.isPending} onClick={() => window.confirm(`仅当你已人工确认远端不存在 Operation ${operationId} 对应动态时继续。确认？`) && run.mutate("absent")}>确认远端不存在</button></div></Panel>
    {operation.error && <DiagnosticPanel diagnostic={diagnosticFromError(operation.error)} defaultOpen />}
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </>;
}
