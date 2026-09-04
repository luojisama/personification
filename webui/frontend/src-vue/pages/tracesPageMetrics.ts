import type { TraceDetail, TraceStage } from "@/api/types";

export type StageFilter = "all" | "issues" | "slow";

export interface TraceDerivedMetrics {
  issueCount: number;
  completedToolCount: number;
  firstErrorIndex: number | null;
  slowStageIndexes: number[];
  upstreamStatus: string;
  upstreamDetailCode: string;
}

const SAFE_DIAGNOSTIC_ATOM = /^[A-Za-z0-9_-]{1,64}$/;

export function outcomeTone(outcome: TraceDetail["outcome"]): "ok" | "warn" | "error" | "unknown" {
  if (outcome === "ok") return "ok";
  if (outcome === "failed") return "error";
  if (outcome === "unknown" || outcome === "partial") return "unknown";
  if (outcome === "no_reply" || outcome === "finished" || outcome === "silent") return "warn";
  return "warn";
}

export function stageTone(status: TraceStage["status"]): "ok" | "warn" | "error" | "unknown" | "running" {
  if (status === "ok") return "ok";
  if (status === "warn") return "warn";
  if (status === "error") return "error";
  if (status === "running") return "running";
  return "unknown";
}

export function stageStatusLabel(status: TraceStage["status"]): string {
  if (status === "ok") return "完成";
  if (status === "running") return "进行中";
  if (status === "warn") return "有告警";
  if (status === "error") return "失败";
  if (status === "skipped") return "已跳过";
  return "未知";
}

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

export function traceTriageText(trace: TraceDetail, metrics: TraceDerivedMetrics): string {
  if (trace.outcome === "unknown" || trace.outcome === "finished") {
    return "本轮最终结果无法确认；请结合发送状态和诊断码核对，确认前不要重试可能已经开始的发送。";
  }
  if (trace.outcome === "no_reply" || trace.outcome === "silent") {
    return `本轮没有发送可见回复；诊断码为 ${trace.diagnosis_code || "no_reply"}，可沿时间线核对静默或提前终止原因。`;
  }
  if (trace.outcome === "partial" && metrics.firstErrorIndex === null) {
    return `本轮仅部分完成；诊断码为 ${trace.diagnosis_code || "partial"}，即使没有错误阶段也需要核对交付与历史状态。`;
  }
  if (trace.outcome === "failed" && metrics.firstErrorIndex === null) {
    return `本轮失败但没有记录明确错误阶段；诊断码为 ${trace.diagnosis_code || "failed"}，请继续核对发送与 Provider 日志。`;
  }
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
