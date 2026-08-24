import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { resources } from "../api/resources";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { useRuntimeEvents } from "../realtime/RuntimeEventsProvider";
import { formatDateTime, formatDuration, formatInteger, shortId } from "../lib/format";
import { traceOutcomeLabel } from "../lib/labels";

export function OverviewPage() {
  const query = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => resources.overview(signal),
  });
  const realtime = useRuntimeEvents();

  return (
    <div className="page-stack">
      <PageHeader
        index="01"
        title="事件总览"
        description="从回复回合、路由证据和恢复队列中提取当前风险；实时流只做增量提示，数据库仍是权威记录。"
        actions={<StateBadge tone={realtime.state === "open" ? "ok" : "running"}>{realtime.state === "open" ? "SSE 在线" : "SSE 连接中"}</StateBadge>}
      />
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {query.data && <OverviewContent data={query.data} />}
      </QueryBoundary>
    </div>
  );
}

function OverviewContent({ data }: { data: Awaited<ReturnType<typeof resources.overview>> }) {
  const runtimeTone = data.runtime_status === "healthy" ? "ok" : data.runtime_status === "offline" ? "error" : "warn";
  return (
    <>
      <section className="metric-rack" aria-label="运行指标">
        <article>
          <span>运行状态</span>
          <strong><StateBadge tone={runtimeTone}>{data.runtime_status === "healthy" ? "健康" : data.runtime_status === "offline" ? "离线" : "降级"}</StateBadge></strong>
          <small>快照 {formatDateTime(data.generated_at)}</small>
        </article>
        <article>
          <span>活跃回合</span>
          <strong>{formatInteger(data.active_turns)}</strong>
          <small>当前进入调度链路</small>
        </article>
        <article>
          <span>一小时事件</span>
          <strong>{formatInteger(data.events_last_hour)}</strong>
          <small>仅脱敏运行事件</small>
        </article>
        <article>
          <span>回合 p95</span>
          <strong>{formatDuration(data.p95_turn_ms)}</strong>
          <small>不含管理异步任务</small>
        </article>
      </section>

      <div className="overview-grid">
        <Panel eyebrow="EVIDENCE / ROUTES" title="路由能力证据">
          <div className="evidence-bars">
            <div><span>支持</span><b>{formatInteger(data.route_counts.supported)}</b><i style={{ "--bar": `${data.route_counts.supported}` } as React.CSSProperties} /></div>
            <div><span>未知</span><b>{formatInteger(data.route_counts.unknown)}</b><i style={{ "--bar": `${data.route_counts.unknown}` } as React.CSSProperties} /></div>
            <div><span>不支持</span><b>{formatInteger(data.route_counts.unsupported)}</b><i style={{ "--bar": `${data.route_counts.unsupported}` } as React.CSSProperties} /></div>
          </div>
          <Link className="text-link" to="/routes">核对每条路由的证据来源 →</Link>
        </Panel>

        <Panel eyebrow="RECOVERY / INBOUND" title="失败恢复队列">
          <dl className="count-ledger">
            <div><dt>待恢复</dt><dd>{formatInteger(data.recovery_counts.pending)}</dd></div>
            <div><dt>处理中</dt><dd>{formatInteger(data.recovery_counts.processing)}</dd></div>
            <div><dt>人工核对区</dt><dd>{formatInteger(data.recovery_counts.quarantined)}</dd></div>
            <div><dt>已过期</dt><dd>{formatInteger(data.recovery_counts.expired)}</dd></div>
          </dl>
          <Link className="text-link" to="/recovery">进入恢复卷宗 →</Link>
        </Panel>

        <Panel className="wide-panel" eyebrow="TRACE / LATEST" title="最近回合">
          {data.latest_traces.length === 0 ? (
            <EmptyState code="trace_list_empty">当前没有可展示的 Trace。</EmptyState>
          ) : (
            <div className="trace-table-wrap">
              <table className="forensic-table">
                <thead><tr><th>开始时间</th><th>Trace ID</th><th>用户</th><th>结果</th><th>耗时</th><th>诊断码</th></tr></thead>
                <tbody>
                  {data.latest_traces.slice(0, 8).map((trace) => (
                    <tr key={trace.trace_id}>
                      <td>{formatDateTime(trace.started_at)}</td>
                      <td><Link to={`/traces/${trace.trace_id}`}><code>{shortId(trace.trace_id, 6)}</code></Link></td>
                      <td>{trace.user_name || trace.user_id}</td>
                      <td>{traceOutcomeLabel(trace.outcome)}</td>
                      <td>{formatDuration(trace.elapsed_ms)}</td>
                      <td><code>{trace.diagnosis_code}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel className="wide-panel" eyebrow="DIAGNOSTICS / OPEN" title="待核对诊断">
          {data.diagnostics.length === 0 ? (
            <EmptyState code="diagnostic_list_empty">没有未处理的运行诊断。</EmptyState>
          ) : (
            <ul className="alert-ledger">
              {data.diagnostics.map((item) => (
                <li key={`${item.code}:${item.trace_id ?? ""}`} data-level={item.level}>
                  <span>{item.title}</span><code>{item.code}</code>{item.trace_id && <small>Trace {shortId(item.trace_id)}</small>}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}
