import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { useBot } from "../app/BotContext";
import { resources } from "../api/resources";
import { IdentityAvatar } from "../components/IdentityAvatar";
import { PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime, formatDuration, formatInteger } from "../lib/format";

function bytes(value: number | null) {
  return value == null ? "暂不可用" : `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function AgentStatusPage() {
  const { botId } = useBot();
  const query = useQuery({
    queryKey: ["agent-runtime", botId],
    queryFn: ({ signal }) => resources.agentRuntime(botId, signal),
    refetchInterval: () => document.hidden ? false : 5_000,
  });
  const data = query.data;
  return (
    <div className="page-stack">
      <PageHeader index="02" title="Agent 状态" description="实时汇总 Bot 连接、回复回合、内心状态、进程资源和最近 Trace。只展示可审计状态，不展示隐藏思维链。" actions={<button className="button button-secondary" type="button" onClick={() => void query.refetch()}>立即刷新</button>} />
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {data && <>
          <Panel eyebrow="BOT / RUNTIME" title="当前 Bot 与 Agent">
            <div className="agent-identity-line">
              <IdentityAvatar src={data.bot.avatar_url} label={data.bot.nickname} size="large" />
              <div><strong>{data.bot.nickname}</strong><code>QQ {data.bot.bot_id || "未连接"}</code></div>
              <StateBadge tone={data.bot.online ? "ok" : "error"}>{data.bot.online ? "协议端在线" : "协议端离线"}</StateBadge>
              <StateBadge tone={data.enabled ? "ok" : "warn"}>{data.enabled ? "Agent 已启用" : "Agent 已停用"}</StateBadge>
              <span>最后活动 {formatDateTime(data.last_active_at)}</span>
            </div>
          </Panel>
          <section className="metric-rack" aria-label="回合状态">
            <article><span>等待缓冲</span><strong>{data.waiting_turns}</strong><small>等待动态截止时间</small></article>
            <article><span>正在生成</span><strong>{data.active_turns}</strong><small>当前活动回复任务</small></article>
            <article><span>正在发送</span><strong>{data.sending_turns}</strong><small>进入发送/确认阶段</small></article>
            <article><span>停滞回合</span><strong>{data.stale_turns}</strong><small>{data.cancelled_turns} 次取消 · {data.gated_turns} 个 gate</small></article>
          </section>
          <div className="overview-grid">
            <Panel eyebrow="INNER STATE" title="可观察内心状态">
              <dl className="safe-settings-view"><div><dt>心情</dt><dd>{data.inner_state.mood || "未记录"}</dd></div><div><dt>精力</dt><dd>{data.inner_state.energy || "未记录"}</dd></div><div><dt>待处理状态</dt><dd>{data.inner_state.pending_count}</dd></div><div><dt>更新时间</dt><dd>{data.inner_state.updated_at || "—"}</dd></div></dl>
            </Panel>
            <Panel eyebrow="PROCESS / LATENCY" title="运行性能">
              <dl className="safe-settings-view"><div><dt>当前 / 峰值内存</dt><dd>{bytes(data.rss_bytes)} / {bytes(data.peak_rss_bytes)}</dd></div><div><dt>事件循环 p50 / p95</dt><dd>{data.event_loop_p50_ms ?? "—"} / {data.event_loop_p95_ms ?? "—"} ms</dd></div><div><dt>回合 p50 / p95</dt><dd>{formatDuration(data.turn_p50_ms)} / {formatDuration(data.turn_p95_ms)}</dd></div><div><dt>后台任务</dt><dd>{data.background_tasks} 个 · {data.background_failures} 次失败 · 缓存 {formatInteger(data.cache_entries)}</dd></div></dl>
            </Panel>
            <Panel className="wide-panel" eyebrow="RECENT TURNS" title="最近回合">
              <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>结果</th><th>Trace</th><th>耗时</th><th>模型</th><th>工具</th><th>会话</th></tr></thead><tbody>{data.recent_traces.map((trace) => <tr key={trace.trace_id}><td><StateBadge tone={trace.state === "stale" ? "warn" : trace.outcome === "ok" ? "ok" : "info"} raw={trace.outcome}>{trace.outcome}</StateBadge></td><td><Link to={`/traces/${trace.trace_id}`}><code>{trace.trace_id}</code></Link></td><td>{formatDuration(trace.elapsed_ms)}</td><td>{trace.model || "未记录"}</td><td>{trace.tool_count}</td><td>{trace.session_type}{trace.group_id ? ` · ${trace.group_id}` : ""}</td></tr>)}</tbody></table></div>
            </Panel>
          </div>
        </>}
      </QueryBoundary>
    </div>
  );
}
