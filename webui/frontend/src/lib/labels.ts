import type { CapabilitySource, CapabilityState, RecoveryStatus, TraceOutcome } from "../api/types";

export function capabilityStateLabel(state: CapabilityState): string {
  return state === "supported" ? "支持" : state === "unsupported" ? "不支持" : "未知";
}

export function capabilitySourceLabel(source: CapabilitySource): string {
  const labels: Record<CapabilitySource, string> = {
    manual: "管理员覆盖",
    runtime_success: "运行成功证据",
    probe: "能力探针",
    provider_catalog: "Provider 能力目录",
    model_catalog: "模型目录",
    heuristic: "保守推断",
  };
  return labels[source];
}

export function traceOutcomeLabel(outcome: TraceOutcome): string {
  const labels: Record<TraceOutcome, string> = {
    ok: "已完成",
    silent: "保持沉默",
    failed: "失败",
    unknown: "结果未知",
    partial: "部分完成",
  };
  return labels[outcome];
}

export function recoveryStatusLabel(status: RecoveryStatus): string {
  const labels: Record<RecoveryStatus, string> = {
    pending: "待恢复",
    processing: "处理中",
    recovered: "已恢复",
    quarantined: "人工核对区",
    expired: "已过期",
    exhausted: "尝试已耗尽",
    abandoned: "已放弃",
  };
  return labels[status];
}

export function sessionTypeLabel(value: string): string {
  if (value === "private") return "私聊";
  if (value === "group") return "群聊";
  if (value === "thread") return "群话题";
  return `未知会话（${value || "unknown"}）`;
}
