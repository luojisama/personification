import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { FunctionalTestDefinition, FunctionalTestRun } from "../api/types";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime, formatDuration } from "../lib/format";

const RISK_LABELS = { local_read: "本地只读", external_read: "外部读取", external_write: "外部写入" } as const;

export function FunctionalTestsPage() {
  const catalog = useQuery({ queryKey: ["functional-health"], queryFn: ({ signal }) => resources.health(signal) });
  const [runs, setRuns] = useState<Record<string, FunctionalTestRun>>({});
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);
  const mutation = useMutation({
    mutationFn: async (test: FunctionalTestDefinition) => {
      const target = targets[test.id] ?? "";
      const prepared = await resources.prepareTestRun(test.id, target);
      if (prepared.state !== "awaiting_confirmation") return prepared;
      const detail = test.risk === "external_write" ? `将准备外部写操作，目标为：${target || "未填写"}。本页不会绕过专用 canary 的目标复核。` : `将调用 ${test.label} 对应的外部服务，可能产生供应商额度消耗。`;
      if (!window.confirm(`${detail}\n\n确认继续吗？`)) return prepared;
      return resources.confirmTestRun(prepared.id, target);
    },
    onSuccess: (run) => { setError(null); setRuns((current) => ({ ...current, [run.test_id]: run })); },
    onError: setError,
  });

  useEffect(() => {
    const pending = Object.values(runs).filter((run) => ["prepared", "running"].includes(run.state));
    if (!pending.length) return;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      for (const run of pending) {
        void resources.testRun(run.id).then((next) => setRuns((current) => ({ ...current, [next.test_id]: next }))).catch(() => undefined);
      }
    }, 1_500);
    return () => window.clearInterval(timer);
  }, [runs]);

  return (
    <div className="page-stack">
      <PageHeader index="04" title="功能体检" description="18 类体检按本地只读、外部读取、外部写入分级。模型与媒体探针会明确确认；公网 HTTP 下外部写测试会被服务端拒绝。" />
      {error != null && <DiagnosticPanel diagnostic={diagnosticFromError(error)} defaultOpen />}
      <QueryBoundary isPending={catalog.isPending} error={catalog.error}>
        {catalog.data && <>
          <Panel eyebrow="RISK-GRADED TESTS" title="体检项目">
            <div className="health-test-grid">
              {catalog.data.tests.map((test) => {
                const run = runs[test.id];
                const busy = mutation.isPending || run?.state === "prepared" || run?.state === "running";
                return <article key={test.id} className="health-test-card">
                  <header><div><strong>{test.label}</strong><small>{test.category}</small></div><StateBadge tone={test.risk === "external_write" ? "warn" : test.risk === "external_read" ? "info" : "ok"}>{RISK_LABELS[test.risk]}</StateBadge></header>
                  {test.risk === "external_write" && <label>目标复核摘要<input value={targets[test.id] ?? ""} onChange={(event) => setTargets((current) => ({ ...current, [test.id]: event.target.value }))} placeholder="Bot、目标 QQ/群或动态 ID" /></label>}
                  <button className="button button-secondary" type="button" disabled={busy} onClick={() => mutation.mutate(test)}>{busy ? "运行中…" : test.risk === "local_read" ? "运行本地检查" : "准备并确认"}</button>
                  {run && <div className="test-run-result"><StateBadge tone={run.state === "succeeded" ? "ok" : run.state === "failed" ? "error" : "running"} raw={run.state}>{run.state}</StateBadge><code>{run.diagnostic_code}</code><span>{formatDuration(run.duration_ms)} · {formatDateTime(run.finished_at)}</span>{Object.keys(run.result_summary).length > 0 && <pre>{JSON.stringify(run.result_summary, null, 2)}</pre>}</div>}
                </article>;
              })}
            </div>
          </Panel>
          {!catalog.data.cached && <EmptyState code="health_cache_empty">尚无全量体检缓存；可按项目运行检查。</EmptyState>}
        </>}
      </QueryBoundary>
    </div>
  );
}
