import type { ReactNode } from "react";

import { diagnosticFromError } from "../api/diagnostics";
import { DiagnosticPanel } from "./DiagnosticPanel";

export function QueryBoundary({ isPending, error, children }: { isPending: boolean; error: unknown; children: ReactNode }) {
  if (isPending) {
    return (
      <div className="loading-ledger" role="status" aria-label="正在读取数据">
        <span /><span /><span />
        <p>正在核对服务端记录…</p>
      </div>
    );
  }
  if (error) return <DiagnosticPanel diagnostic={diagnosticFromError(error)} defaultOpen />;
  return children;
}
