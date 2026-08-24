import { api } from "./client";
import type {
  OperationDiagnostic,
  OverviewSnapshot,
  Page,
  PersonaListItem,
  GroupListItem,
  StickerPage,
  ConfigListItem,
  CatalogItem,
  CursorPage,
  MultimodalRouteSnapshot,
  RecoveryItem,
  RouteCapabilityItem,
  TraceDetail,
  TraceListItem,
} from "./types";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function traceOutcome(value: unknown): TraceListItem["outcome"] {
  return value === "ok" || value === "silent" || value === "failed" || value === "partial"
    ? value
    : "unknown";
}

export function sanitizeTraceDetail(raw: unknown): TraceDetail {
  const source = isRecord(raw) ? raw : {};
  const decision = isRecord(source.decision) ? source.decision : {};
  const rawStages = Array.isArray(source.stages) ? source.stages : [];
  const rawTools = Array.isArray(source.tools) ? source.tools : [];

  return {
    trace_id: text(source.trace_id),
    started_at: text(source.started_at),
    finished_at: text(source.finished_at) || null,
    session_type: text(source.session_type, "unknown"),
    group_id: text(source.group_id) || null,
    user_id: text(source.user_id),
    user_name: text(source.user_name, "未知用户"),
    avatar_url: text(source.avatar_url) || null,
    outcome: traceOutcome(source.outcome),
    diagnosis_code: text(source.diagnosis_code, "trace_unclassified"),
    input_summary: text(source.input_summary).slice(0, 2_000),
    elapsed_ms: finiteNumber(source.elapsed_ms),
    bot_id: text(source.bot_id),
    media_summary: Array.isArray(source.media_summary)
      ? source.media_summary.map((item) => text(item).slice(0, 240)).filter(Boolean).slice(0, 20)
      : [],
    decision: {
      summary: text(decision.summary).slice(0, 1_000),
      action: text(decision.action, "unknown"),
      tier: finiteNumber(decision.tier),
      wait_seconds: finiteNumber(decision.wait_seconds),
      interest: finiteNumber(decision.interest),
      reason_code: text(decision.reason_code, "decision_unavailable"),
    },
    stages: rawStages.slice(0, 200).map((value) => {
      const stage = isRecord(value) ? value : {};
      const status = text(stage.status, "unknown");
      return {
        key: text(stage.key, "unknown"),
        label: text(stage.label, "未命名阶段"),
        status:
          status === "pending" ||
          status === "running" ||
          status === "ok" ||
          status === "warn" ||
          status === "error" ||
          status === "skipped"
            ? status
            : "unknown",
        started_at: text(stage.started_at) || null,
        finished_at: text(stage.finished_at) || null,
        duration_ms: finiteNumber(stage.duration_ms),
        summary: text(stage.summary).slice(0, 1_000),
        detail_code: text(stage.detail_code, "stage_unclassified"),
        remaining_ms: finiteNumber(stage.remaining_ms),
      };
    }),
    tools: rawTools.slice(0, 100).map((value) => {
      const tool = isRecord(value) ? value : {};
      return {
        name: text(tool.name, "unknown_tool"),
        namespace: text(tool.namespace, "default"),
        status: text(tool.status, "unknown"),
        duration_ms: finiteNumber(tool.duration_ms),
        argument_summary: text(tool.argument_summary).slice(0, 500),
        result_summary: text(tool.result_summary).slice(0, 1_000),
        schema_hash: text(tool.schema_hash).slice(0, 128),
        detail_code: text(tool.detail_code, "tool_unclassified"),
      };
    }),
    final_reply: text(source.final_reply).slice(0, 6_000),
    send_status: text(source.send_status, "unknown"),
    history_status: text(source.history_status, "unknown"),
    recovery_ids: Array.isArray(source.recovery_ids)
      ? source.recovery_ids.filter((item): item is number => typeof item === "number").slice(0, 100)
      : [],
  };
}

export const resources = {
  overview(signal?: AbortSignal): Promise<OverviewSnapshot> {
    return api.get("/overview", undefined, signal);
  },
  routes(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<RouteCapabilityItem>> {
    return api.get("/routes/capabilities", { page, page_size: pageSize, search }, signal);
  },
  queueRouteProbe(routeFingerprint: string): Promise<OperationDiagnostic> {
    return api.post(`/routes/capabilities/${encodeURIComponent(routeFingerprint)}/probes`, {
      mode: "all",
    });
  },
  traces(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<TraceListItem>> {
    return api.get("/traces", { page, page_size: pageSize, search }, signal);
  },
  async trace(traceId: string, signal?: AbortSignal): Promise<TraceDetail> {
    const raw = await api.get<unknown>(`/traces/${encodeURIComponent(traceId)}`, undefined, signal);
    return sanitizeTraceDetail(raw);
  },
  recovery(
    page = 1,
    pageSize = 20,
    status = "",
    signal?: AbortSignal,
  ): Promise<Page<RecoveryItem>> {
    return api.get("/recovery", { page, page_size: pageSize, status }, signal);
  },
  abandonRecovery(id: number): Promise<OperationDiagnostic> {
    return api.post(`/recovery-queue/${id}/abandon`);
  },
  retryRecovery(id: number): Promise<OperationDiagnostic> {
    return api.post(`/recovery-queue/${id}/retry`, { confirmed_not_sent: true });
  },
  runtimeSettings(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/settings", undefined, signal);
  },
  personas(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<PersonaListItem>> {
    return api.get("/personas", { page, page_size: pageSize, search }, signal);
  },
  groups(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<GroupListItem>> {
    return api.get("/groups", { page, page_size: pageSize, search }, signal);
  },
  stickers(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<StickerPage> {
    return api.get("/stickers", { page, page_size: pageSize, search }, signal);
  },
  rebuildStickerIndex(): Promise<OperationDiagnostic> {
    return api.post("/stickers/index/rebuild");
  },
  config(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<ConfigListItem>> {
    return api.get("/config", { page, page_size: pageSize, search }, signal);
  },
  catalog(
    dataset: "plugin-knowledge" | "mcp" | "skills" | "tool-tasks" | "memories",
    page = 1,
    pageSize = 20,
    search = "",
    signal?: AbortSignal,
  ): Promise<Page<CatalogItem>> {
    return api.get(`/${dataset}`, { page, page_size: pageSize, search }, signal);
  },
  logs(limit = 100, cursor = 0, search = "", signal?: AbortSignal): Promise<CursorPage<CatalogItem>> {
    return api.get("/logs", { limit, cursor, search }, signal);
  },
  multimodalRoutes(signal?: AbortSignal): Promise<MultimodalRouteSnapshot> {
    return api.get("/multimodal/routes", undefined, signal);
  },
  qzoneCapabilities(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/qzone/capabilities", undefined, signal);
  },
};
