import { useCallback, useEffect, useState } from "react";

import type { OperationDiagnostic } from "../api/types";
import { safeDiagnostic } from "../api/diagnostics";
import { Icon } from "./Icon";

const HISTORY_PREFIX = "personification.console.diagnostics.";

export function useDiagnosticHistory(scope: string): {
  diagnostics: OperationDiagnostic[];
  record: (diagnostic: OperationDiagnostic) => void;
  clear: () => void;
} {
  const key = `${HISTORY_PREFIX}${scope}`;
  const [diagnostics, setDiagnostics] = useState<OperationDiagnostic[]>(() => {
    try {
      const parsed: unknown = JSON.parse(window.sessionStorage.getItem(key) ?? "[]");
      return Array.isArray(parsed) ? parsed.map((item) => safeDiagnostic(item as Partial<OperationDiagnostic>)).slice(0, 12) : [];
    } catch {
      return [];
    }
  });

  useEffect(() => {
    try {
      window.sessionStorage.setItem(key, JSON.stringify(diagnostics));
    } catch {
      // 诊断仍保留在当前 React 状态中。
    }
  }, [diagnostics, key]);

  const record = useCallback((diagnostic: OperationDiagnostic) => {
    const safe = safeDiagnostic(diagnostic);
    setDiagnostics((current) => {
      const fingerprint = safe.operation_id || safe.trace_id || `${safe.code}:${safe.phase}:${safe.message}`;
      return [safe, ...current.filter((item) => (item.operation_id || item.trace_id || `${item.code}:${item.phase}:${item.message}`) !== fingerprint)].slice(0, 12);
    });
  }, []);

  const clear = useCallback(() => setDiagnostics([]), []);
  return { diagnostics, record, clear };
}

export function DiagnosticPanel({ diagnostic, defaultOpen = false }: { diagnostic: OperationDiagnostic; defaultOpen?: boolean }) {
  const tone = diagnostic.ok ? "ok" : diagnostic.outcome_unknown ? "unknown" : "error";
  return (
    <details className={`diagnostic-panel diagnostic-${tone}`} open={defaultOpen}>
      <summary>
        <span className="diagnostic-icon" aria-hidden="true">
          <Icon name={diagnostic.ok ? "check" : diagnostic.outcome_unknown ? "unknown" : "close"} />
        </span>
        <span className="diagnostic-title">
          <strong>{diagnostic.title}</strong>
          <small>{diagnostic.phase}</small>
        </span>
        <code className="operation-code">{diagnostic.code}</code>
        <Icon className="diagnostic-chevron" name="chevron" />
      </summary>
      <div className="diagnostic-body">
        <p>{diagnostic.message || "服务端没有提供更多安全说明。"}</p>
        {diagnostic.outcome_unknown && (
          <div className="unknown-warning">
            结果未知：界面不会自动重试。请先人工核对外部系统。
          </div>
        )}
        {diagnostic.steps.length > 0 && (
          <ol className="diagnostic-steps">
            {diagnostic.steps.map((step) => (
              <li key={step.key} data-status={step.status}>
                <strong>{step.label}</strong>
                {step.message && <span>{step.message}</span>}
              </li>
            ))}
          </ol>
        )}
        {diagnostic.suggestion && <p className="diagnostic-suggestion">建议：{diagnostic.suggestion}</p>}
        <dl className="diagnostic-identifiers">
          {diagnostic.operation_id && <><dt>Operation ID</dt><dd><code>{diagnostic.operation_id}</code></dd></>}
          {diagnostic.trace_id && <><dt>Trace ID</dt><dd><code>{diagnostic.trace_id}</code></dd></>}
          <dt>允许直接重试</dt><dd>{diagnostic.retryable ? "是" : "否"}</dd>
        </dl>
      </div>
    </details>
  );
}
