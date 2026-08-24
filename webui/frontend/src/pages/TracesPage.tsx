import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { resources } from "../api/resources";
import type { TraceDetail, TraceStage } from "../api/types";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime, formatDuration, shortId } from "../lib/format";
import { sessionTypeLabel, traceOutcomeLabel } from "../lib/labels";

function outcomeTone(outcome: TraceDetail["outcome"]): "ok" | "warn" | "error" | "unknown" {
  if (outcome === "ok") return "ok";
  if (outcome === "failed") return "error";
  if (outcome === "unknown" || outcome === "partial") return "unknown";
  return "warn";
}

function stageTone(status: TraceStage["status"]): "ok" | "warn" | "error" | "unknown" | "running" | "info" {
  if (status === "ok") return "ok";
  if (status === "warn") return "warn";
  if (status === "error") return "error";
  if (status === "running") return "running";
  if (status === "unknown") return "unknown";
  return "info";
}

export function TracesPage() {
  const { traceId } = useParams();
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const list = useQuery({
    queryKey: ["traces", page, search],
    queryFn: ({ signal }) => resources.traces(page, 20, search, signal),
  });
  const detail = useQuery({
    queryKey: ["trace-detail", traceId],
    queryFn: ({ signal }) => resources.trace(traceId ?? "", signal),
    enabled: Boolean(traceId),
  });

  useEffect(() => {
    if (!traceId && list.data?.items[0]?.trace_id) {
      void navigate(`/traces/${list.data.items[0].trace_id}`, { replace: true });
    }
  }, [list.data, navigate, traceId]);

  return (
    <div className="page-stack trace-page">
      <PageHeader
        index="03"
        title="Trace 取证"
        description="只展示可审计决策摘要、状态机、阶段预算和脱敏工具证据；不请求、不存储也不渲染模型隐藏思维链。"
        actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="搜索 Trace、用户或诊断码" />}
      />
      <div className="trace-workbench">
        <Panel className="trace-list-pane" eyebrow="INDEX / TURNS" title="回合索引">
          <QueryBoundary isPending={list.isPending} error={list.error}>
            {list.data && list.data.items.length === 0 ? (
              <EmptyState code="trace_list_empty">没有匹配的 Trace。</EmptyState>
            ) : (
              <div className="trace-index-list">
                {list.data?.items.map((trace) => (
                  <Link className={trace.trace_id === traceId ? "active" : ""} to={`/traces/${trace.trace_id}`} key={trace.trace_id}>
                    <div className="trace-index-head">
                      <code>{shortId(trace.trace_id, 6)}</code>
                      <time>{formatDateTime(trace.started_at)}</time>
                    </div>
                    <strong>{trace.user_name || trace.user_id}</strong>
                    <p>{trace.input_summary || "没有可展示的消息摘要"}</p>
                    <div><StateBadge tone={outcomeTone(trace.outcome)}>{traceOutcomeLabel(trace.outcome)}</StateBadge><span>{formatDuration(trace.elapsed_ms)}</span></div>
                  </Link>
                ))}
              </div>
            )}
          </QueryBoundary>
          {list.data && <Pagination page={list.data.page} totalPages={list.data.total_pages} onChange={setPage} />}
        </Panel>

        <QueryBoundary isPending={Boolean(traceId) && detail.isPending} error={detail.error}>
          {detail.data ? <TraceEvidence trace={detail.data} /> : <EmptyState code="trace_not_selected">从左侧选择一条 Trace 开始核对。</EmptyState>}
        </QueryBoundary>
      </div>
    </div>
  );
}

function TraceEvidence({ trace }: { trace: TraceDetail }) {
  return (
    <>
      <Panel className="trace-timeline-pane" eyebrow="TIMELINE / OBSERVABLE" title="阶段时间线">
        <div className="trace-case-head">
          <div className="avatar-stamp" aria-hidden="true">
            {trace.avatar_url ? <img src={trace.avatar_url} alt="" referrerPolicy="no-referrer" /> : (trace.user_name || "?").slice(0, 1)}
          </div>
          <div><strong>{trace.user_name || "未知用户"}</strong><span>QQ {trace.user_id || "—"} · {sessionTypeLabel(trace.session_type)}</span></div>
          <StateBadge tone={outcomeTone(trace.outcome)} raw={trace.outcome}>{traceOutcomeLabel(trace.outcome)}</StateBadge>
        </div>

        <article className="message-evidence">
          <span>当前批次 · 安全摘要</span>
          <p>{trace.input_summary || "消息内容未进入可见 Trace。"}</p>
          {trace.media_summary.length > 0 && <ul>{trace.media_summary.map((item, index) => <li key={`${index}:${item}`}>{item}</li>)}</ul>}
        </article>

        <ol className="timeline-list">
          {trace.stages.map((stage, index) => (
            <li key={`${stage.key}:${index}`} data-status={stage.status}>
              <span className="timeline-node" aria-hidden="true" />
              <div className="timeline-card">
                <header>
                  <div><span>{String(index + 1).padStart(2, "0")}</span><strong>{stage.label}</strong></div>
                  <StateBadge tone={stageTone(stage.status)} raw={stage.status}>{stage.status === "ok" ? "完成" : stage.status === "running" ? "进行中" : stage.status === "warn" ? "有告警" : stage.status === "error" ? "失败" : stage.status === "skipped" ? "已跳过" : "未知"}</StateBadge>
                </header>
                {stage.summary && <p>{stage.summary}</p>}
                <footer><code>{stage.detail_code}</code><span>{formatDuration(stage.duration_ms)}</span>{stage.remaining_ms !== null && <span>剩余预算 {formatDuration(stage.remaining_ms)}</span>}</footer>
              </div>
            </li>
          ))}
        </ol>
      </Panel>

      <Panel className="trace-detail-pane" eyebrow="CONTEXT / AUDIT" title="审计详情">
        <section className="audit-section">
          <h3>Agent 决策摘要</h3>
          <p>{trace.decision.summary || "本轮没有可展示的结构化决策摘要。"}</p>
          <dl className="audit-grid">
            <div><dt>动作</dt><dd>{trace.decision.action}</dd></div>
            <div><dt>参与等级</dt><dd>{trace.decision.tier ?? "—"}</dd></div>
            <div><dt>等待</dt><dd>{trace.decision.wait_seconds === null ? "—" : `${trace.decision.wait_seconds} 秒`}</dd></div>
            <div><dt>兴趣</dt><dd>{trace.decision.interest === null ? "—" : trace.decision.interest.toFixed(2)}</dd></div>
          </dl>
          <code>{trace.decision.reason_code}</code>
        </section>

        <section className="audit-section">
          <h3>工具步骤</h3>
          {trace.tools.length === 0 ? <p className="muted">本轮没有脱敏工具记录。</p> : trace.tools.map((tool, index) => (
            <details className="tool-evidence" key={`${tool.name}:${index}`}>
              <summary><span>{tool.namespace} / {tool.name}</span><StateBadge tone={tool.status === "ok" ? "ok" : "warn"} raw={tool.status}>{tool.status === "ok" ? "完成" : "需核对"}</StateBadge></summary>
              <dl>
                <div><dt>参数摘要</dt><dd>{tool.argument_summary || "未记录"}</dd></div>
                <div><dt>结果摘要</dt><dd>{tool.result_summary || "未记录"}</dd></div>
                <div><dt>Schema hash</dt><dd><code>{tool.schema_hash || "—"}</code></dd></div>
                <div><dt>诊断码</dt><dd><code>{tool.detail_code}</code></dd></div>
              </dl>
            </details>
          ))}
        </section>

        <section className="audit-section final-output-evidence">
          <h3>最终可见回复</h3>
          <blockquote>{trace.final_reply || "本轮没有发送可见回复。"}</blockquote>
          <dl className="audit-grid">
            <div><dt>发送结果</dt><dd>{trace.send_status}</dd></div>
            <div><dt>历史提交</dt><dd>{trace.history_status}</dd></div>
          </dl>
        </section>

        <section className="audit-section trace-identifiers">
          <h3>关联标识</h3>
          <dl>
            <dt>Trace ID</dt><dd><code>{trace.trace_id}</code></dd>
            <dt>Bot ID</dt><dd><code>{trace.bot_id || "—"}</code></dd>
            <dt>诊断码</dt><dd><code>{trace.diagnosis_code}</code></dd>
            <dt>恢复项</dt><dd>{trace.recovery_ids.length ? trace.recovery_ids.join("、") : "无"}</dd>
          </dl>
        </section>
      </Panel>
    </>
  );
}
