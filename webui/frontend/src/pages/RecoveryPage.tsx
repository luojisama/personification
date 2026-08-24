import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { RecoveryItem, RecoveryStatus } from "../api/types";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime, shortId } from "../lib/format";
import { recoveryStatusLabel, sessionTypeLabel } from "../lib/labels";

const FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部" },
  { value: "pending", label: "待恢复" },
  { value: "processing", label: "处理中" },
  { value: "quarantined", label: "人工核对区" },
  { value: "recovered", label: "已恢复" },
  { value: "expired", label: "已过期" },
];

function recoveryTone(status: RecoveryStatus): "ok" | "warn" | "error" | "unknown" | "running" | "info" {
  if (status === "recovered") return "ok";
  if (status === "processing") return "running";
  if (status === "quarantined") return "unknown";
  if (status === "expired" || status === "exhausted") return "error";
  if (status === "pending") return "warn";
  return "info";
}

export function RecoveryPage() {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const query = useQuery({
    queryKey: ["recovery", page, status],
    queryFn: ({ signal }) => resources.recovery(page, 20, status, signal),
  });
  const history = useDiagnosticHistory("recovery");

  return (
    <div className="page-stack">
      <PageHeader
        index="09"
        title="恢复队列"
        description="队列保存失败的入站消息摘要，并使用当前上下文重新生成。发送结果未知或部分送达时只进入人工核对区，绝不自动重放。"
        actions={
          <label className="select-field"><span>状态</span><select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>{FILTERS.map((filter) => <option key={filter.value} value={filter.value}>{filter.label}</option>)}</select></label>
        }
      />
      {history.diagnostics.length > 0 && (
        <div className="diagnostic-stack">
          {history.diagnostics.map((diagnostic, index) => <DiagnosticPanel key={diagnostic.operation_id || diagnostic.trace_id || `${diagnostic.code}:${index}`} diagnostic={diagnostic} defaultOpen={index === 0} />)}
        </div>
      )}
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {query.data && query.data.items.length === 0 ? (
          <EmptyState code="recovery_queue_empty">当前筛选条件下没有恢复项。</EmptyState>
        ) : (
          <div className="recovery-list">
            {query.data?.items.map((item) => <RecoveryDossier key={item.id} item={item} recordDiagnostic={history.record} />)}
          </div>
        )}
      </QueryBoundary>
      {query.data && <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={setPage} />}
    </div>
  );
}

function RecoveryDossier({ item, recordDiagnostic }: { item: RecoveryItem; recordDiagnostic: ReturnType<typeof useDiagnosticHistory>["record"] }) {
  const queryClient = useQueryClient();
  const action = useMutation({
    mutationFn: async (kind: "abandon" | "retry") => kind === "abandon" ? resources.abandonRecovery(item.id) : resources.retryRecovery(item.id),
    onSuccess: (diagnostic) => {
      recordDiagnostic(diagnostic);
      void queryClient.invalidateQueries({ queryKey: ["recovery"] });
    },
    onError: (error) => recordDiagnostic(diagnosticFromError(error)),
  });
  const canAbandon = item.status === "pending" || item.status === "quarantined";
  const canConfirmRetry = item.status === "quarantined" && item.failure_class === "delivery_unknown";

  const retry = () => {
    const confirmed = window.confirm("请确认你已在 QQ 或外部系统核对：这条消息明确没有送达。确认后系统才会重新生成，不会重放旧回复。是否继续？");
    if (confirmed) action.mutate("retry");
  };

  return (
    <Panel as="article" className="recovery-dossier" eyebrow={`RECOVERY / ${String(item.id).padStart(6, "0")}`} title={item.safe_summary || "没有可展示的入站摘要"}>
      <div className="recovery-status-line">
        <StateBadge tone={recoveryTone(item.status)} raw={item.status}>{recoveryStatusLabel(item.status)}</StateBadge>
        <span>{sessionTypeLabel(item.session_type)} · {item.session_id}</span>
        <span>尝试 {item.attempts} / 3 次</span>
      </div>
      <dl className="recovery-evidence-grid">
        <div><dt>失败分类</dt><dd><code>{item.failure_class}</code></dd></div>
        <div><dt>失败阶段</dt><dd><code>{item.failure_stage}</code></dd></div>
        <div><dt>首次失败</dt><dd>{formatDateTime(item.first_failed_at)}</dd></div>
        <div><dt>过期时间</dt><dd>{formatDateTime(item.expires_at)}</dd></div>
        <div><dt>原消息 ID</dt><dd><code>{shortId(item.message_id)}</code></dd></div>
        <div><dt>Trace ID</dt><dd><code>{shortId(item.trace_id)}</code></dd></div>
      </dl>
      {item.outcome_unknown && <div className="unknown-warning">发送结果未知：已禁止自动恢复。只有确认未发送后才可重新开放。</div>}
      {item.missing_segments.length > 0 && <div className="unknown-warning">部分发送缺失分段：{item.missing_segments.join("、")}。禁止整批重放。</div>}
      {(canAbandon || canConfirmRetry) && (
        <footer className="dossier-actions">
          {canConfirmRetry && <button className="button button-primary" type="button" disabled={action.isPending} onClick={retry}>确认未发送并重试</button>}
          {canAbandon && <button className="button button-danger" type="button" disabled={action.isPending} onClick={() => action.mutate("abandon")}>放弃此恢复项</button>}
        </footer>
      )}
    </Panel>
  );
}
