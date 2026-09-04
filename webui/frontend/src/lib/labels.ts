import type { CapabilitySource, CapabilityState, RecoveryStatus, TraceOutcome, VerificationState } from "../api/types";

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

export function verificationStateLabel(state: VerificationState): string {
  const labels: Record<VerificationState, string> = {
    verified: "已验证",
    not_run: "未运行",
    probe_unavailable: "探针不可用",
    inconclusive: "结果不确定",
    stale: "证据已过期",
  };
  return labels[state];
}

export function traceOutcomeLabel(outcome: TraceOutcome): string {
  const labels: Record<TraceOutcome, string> = {
    ok: "已完成",
    silent: "保持沉默",
    no_reply: "未发送可见回复",
    finished: "流程提前结束",
    failed: "失败",
    unknown: "结果未知",
    partial: "部分完成",
  };
  return labels[outcome];
}

export function traceDeliveryStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    confirmed: "已确认送达",
    unconfirmed: "发送结果未知，请勿直接重试",
    partial: "部分送达",
    not_started: "尚未开始发送",
    ok: "已完成",
    no_reply: "未发送可见回复",
    failed: "发送失败",
    unknown: "结果未知",
  };
  return labels[value] ?? `未知状态（${value || "unknown"}）`;
}

export function traceHistoryStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    committed: "已提交",
    confirmed: "已确认提交",
    skipped: "未提交",
    partial: "部分提交",
    unknown: "状态未知",
  };
  return labels[value] ?? `未知状态（${value || "unknown"}）`;
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
