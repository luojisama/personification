import { ApiError } from "./client";
import type { OperationDiagnostic } from "./types";

const CODE_LABELS: Record<string, string> = {
  api_request_failed: "API 请求失败",
  network_unavailable: "网络连接不可用",
  unauthorized: "管理员会话已失效",
  forbidden: "当前管理员无权执行此操作",
  not_found: "未找到请求的记录",
  validation_error: "请求参数未通过校验",
  route_capability_unverified: "路由能力尚未验证",
  capability_unverified: "能力证据尚未建立",
  provider_timeout: "Provider 请求超时",
  probe_queued: "能力探针已加入队列",
  recovery_delivery_unknown: "发送结果未知，已隔离",
  recovery_delivery_partial: "消息仅部分送达，已隔离",
  recovery_abandoned: "恢复项已放弃",
  resync_required: "实时游标已过期，需要重新同步",
  sse_connection_failed: "实时事件流连接失败",
  git_source_benchmark_ready: "更新源测速完成",
  git_source_benchmark_all_failed: "所有更新源测速失败",
  plugin_update_already_current: "插件已经是最新版本",
  plugin_update_applied: "插件更新完成",
  plugin_update_dirty: "工作区存在未提交修改",
  plugin_update_diverged: "本地与远端提交已分叉",
  plugin_update_verification_unknown: "插件更新结果需要人工核对",
};

export function diagnosticCodeLabel(code: string): string {
  return CODE_LABELS[code] ?? `未知诊断（${code || "unknown"}）`;
}

export function diagnosticFromError(error: unknown): OperationDiagnostic {
  if (error instanceof ApiError) {
    const fallbackCode =
      error.status === 401
        ? "unauthorized"
        : error.status === 403
          ? "forbidden"
          : error.status === 404
            ? "not_found"
            : error.status === 422
              ? "validation_error"
              : error.code;
    return {
      ok: false,
      code: fallbackCode,
      phase: error.phase,
      title: diagnosticCodeLabel(fallbackCode),
      message: error.outcomeUnknown
        ? "服务端无法确认外部操作结果。请先人工核对，当前界面不会自动重试。"
        : "请求未完成。请依据诊断码和 Trace ID 核对服务端状态。",
      retryable: error.retryable,
      partial: false,
      outcome_unknown: error.outcomeUnknown,
      operation_id: error.operationId || undefined,
      trace_id: error.traceId || undefined,
      warnings: [],
      suggestion: error.outcomeUnknown ? "先核对 QQ、QZone 或外部服务的实际结果。" : undefined,
      steps: [],
    };
  }
  return {
    ok: false,
    code: "network_unavailable",
    phase: "http_request",
    title: diagnosticCodeLabel("network_unavailable"),
    message: "浏览器没有取得可解析的服务端响应。",
    retryable: true,
    partial: false,
    outcome_unknown: false,
    warnings: [],
    suggestion: "检查 WebUI 服务、反向代理和当前网络后再试。",
    steps: [],
  };
}

export function safeDiagnostic(raw: Partial<OperationDiagnostic>): OperationDiagnostic {
  const code = String(raw.code || "api_request_failed");
  return {
    ok: raw.ok === true,
    code,
    phase: String(raw.phase || "unknown"),
    title: String(raw.title || diagnosticCodeLabel(code)).slice(0, 120),
    message: String(raw.message || "").slice(0, 500),
    retryable: raw.retryable === true,
    partial: raw.partial === true,
    outcome_unknown: raw.outcome_unknown === true,
    operation_id: raw.operation_id ? String(raw.operation_id).slice(0, 128) : undefined,
    trace_id: raw.trace_id ? String(raw.trace_id).slice(0, 128) : undefined,
    warnings: Array.isArray(raw.warnings)
      ? raw.warnings.map((item) => String(item).slice(0, 240)).slice(0, 8)
      : [],
    suggestion: raw.suggestion ? String(raw.suggestion).slice(0, 300) : undefined,
    steps: Array.isArray(raw.steps)
      ? raw.steps.slice(0, 20).map((step) => ({
          key: String(step.key || "unknown").slice(0, 80),
          label: String(step.label || "未命名步骤").slice(0, 120),
          status: step.status,
          message: step.message ? String(step.message).slice(0, 300) : undefined,
        }))
      : [],
  };
}
