import { useEffect, useMemo, useState } from "react";
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

type StageFilter = "all" | "issues" | "slow";

export interface TraceDerivedMetrics {
  issueCount: number;
  completedToolCount: number;
  firstErrorIndex: number | null;
  slowStageIndexes: number[];
  upstreamStatus: string;
  upstreamDetailCode: string;
}

const SAFE_DIAGNOSTIC_ATOM = /^[A-Za-z0-9_-]{1,64}$/;

export function deriveTraceMetrics(trace: TraceDetail): TraceDerivedMetrics {
  const issueCount = trace.stages.filter((stage) => stage.status === "warn" || stage.status === "error").length;
  const firstErrorIndex = trace.stages.findIndex((stage) => stage.status === "error");
  const slowStageIndexes = trace.stages
    .map((stage, index) => ({ index, duration: stage.duration_ms ?? -1 }))
    .filter((item) => item.duration >= 0)
    .sort((left, right) => right.duration - left.duration || left.index - right.index)
    .slice(0, 3)
    .map((item) => item.index);
  const failureDetail = trace.stages.find((stage) => stage.key === "provider_failure")?.summary ?? "";
  const upstreamMatch = failureDetail.match(/(?:^|\|)upstream:([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)/);
  const upstreamStatus = upstreamMatch?.[1] && upstreamMatch[1] !== "-" && SAFE_DIAGNOSTIC_ATOM.test(upstreamMatch[1]) ? upstreamMatch[1] : "";
  const upstreamDetailCode = upstreamMatch?.[2] && upstreamMatch[2] !== "-" && SAFE_DIAGNOSTIC_ATOM.test(upstreamMatch[2]) ? upstreamMatch[2] : "";
  return {
    issueCount,
    completedToolCount: trace.tools.filter((tool) => tool.status === "ok" && tool.detail_code === "result").length,
    firstErrorIndex: firstErrorIndex >= 0 ? firstErrorIndex : null,
    slowStageIndexes,
    upstreamStatus,
    upstreamDetailCode,
  };
}

function traceTriageText(trace: TraceDetail, metrics: TraceDerivedMetrics): string {
  if (trace.diagnosis_code === "provider_request_rejected") {
    const firstError = metrics.firstErrorIndex === null ? null : trace.stages[metrics.firstErrorIndex];
    const failureLocation = firstError?.key === "provider_failure"
      ? "首个错误为 Provider 调用失败。"
      : `本轮最终诊断为 Provider 请求被拒绝；首个错误阶段为“${firstError?.label || "未记录"}”。`;
    const upstream = metrics.upstreamStatus
      ? `上游分类为 ${metrics.upstreamStatus}${metrics.upstreamDetailCode ? ` / ${metrics.upstreamDetailCode}` : ""}。`
      : "HTTP 400 的具体上游分类尚未记录。";
    return `已确认成功返回 ${metrics.completedToolCount} 条工具结果；${failureLocation}${upstream}请结合脱敏 Provider 日志和请求形状继续核对。`;
  }
  if (metrics.firstErrorIndex !== null) {
    return `首个错误出现在“${trace.stages[metrics.firstErrorIndex]?.label || "未知阶段"}”；当前共有 ${metrics.issueCount} 个错误或告警阶段。`;
  }
  if (metrics.issueCount > 0) {
    return `本轮没有错误阶段，但有 ${metrics.issueCount} 个告警阶段需要核对。`;
  }
  return "本轮未发现错误或告警阶段，可继续核对最终发送与历史提交状态。";
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
      void navigate(`/runtime/traces/timeline/${list.data.items[0].trace_id}`, { replace: true });
    }
  }, [list.data, navigate, traceId]);

  return (
    <div className="page-stack trace-page">
      <PageHeader
        index="08"
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
                  <Link className={trace.trace_id === traceId ? "active" : ""} to={`/runtime/traces/timeline/${trace.trace_id}`} key={trace.trace_id}>
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

export function TraceEvidence({ trace }: { trace: TraceDetail }) {
  const [stageFilter, setStageFilter] = useState<StageFilter>("all");
  const metrics = useMemo(() => deriveTraceMetrics(trace), [trace]);
  const visibleStages = useMemo(() => trace.stages
    .map((stage, index) => ({ stage, index }))
    .filter(({ stage, index }) => {
      if (stageFilter === "issues") return stage.status === "warn" || stage.status === "error";
      if (stageFilter === "slow") return metrics.slowStageIndexes.includes(index);
      return true;
    }), [metrics.slowStageIndexes, stageFilter, trace.stages]);

  useEffect(() => setStageFilter("all"), [trace.trace_id]);

  function jumpToFirstError() {
    if (metrics.firstErrorIndex === null) return;
    setStageFilter("all");
    window.requestAnimationFrame(() => {
      const target = document.getElementById(`trace-stage-${metrics.firstErrorIndex}`);
      if (!target) return;
      const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
      target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
      target.focus({ preventScroll: true });
    });
  }

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

        <section className="trace-triage" aria-labelledby="trace-triage-title">
          <div>
            <span>TRIAGE / FIRST FAILURE</span>
            <h3 id="trace-triage-title">本轮诊断摘要</h3>
            <p>{traceTriageText(trace, metrics)}</p>
          </div>
          <dl>
            <div><dt>错误与告警</dt><dd>{metrics.issueCount}</dd></div>
            <div><dt>成功返回的工具结果</dt><dd>{metrics.completedToolCount}</dd></div>
            <div><dt>首个错误</dt><dd>{metrics.firstErrorIndex === null ? "无" : `阶段 ${metrics.firstErrorIndex + 1}`}</dd></div>
          </dl>
          {metrics.firstErrorIndex !== null && <button type="button" className="button button-quiet" onClick={jumpToFirstError}>跳到首个错误</button>}
        </section>

        <div className="trace-stage-toolbar">
          <div className="filter-chips" aria-label="时间线筛选">
            <button type="button" aria-pressed={stageFilter === "all"} onClick={() => setStageFilter("all")}>全部 {trace.stages.length}</button>
            <button type="button" aria-pressed={stageFilter === "issues"} onClick={() => setStageFilter("issues")}>问题 {metrics.issueCount}</button>
            <button type="button" aria-pressed={stageFilter === "slow"} onClick={() => setStageFilter("slow")}>最慢 {metrics.slowStageIndexes.length}</button>
          </div>
          <span>筛选只改变展示，不改变原始 Trace。</span>
        </div>

        <ol className="timeline-list">
          {visibleStages.map(({ stage, index }) => (
            <li id={`trace-stage-${index}`} tabIndex={-1} key={`${stage.key}:${index}`} data-status={stage.status}>
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
        {visibleStages.length === 0 && <p className="muted trace-filter-empty">当前筛选下没有阶段。</p>}
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
