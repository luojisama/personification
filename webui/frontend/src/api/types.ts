export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export type CapabilityState = "supported" | "unsupported" | "unknown";
export type CapabilitySource =
  | "manual"
  | "runtime_success"
  | "probe"
  | "provider_catalog"
  | "model_catalog"
  | "heuristic";

export interface RouteCapability {
  state: CapabilityState;
  source: CapabilitySource;
  checked_at: string | number | null;
  expires_at: string | number | null;
  detail_code: string;
}

export type CapabilityName =
  | "image_input"
  | "audio_input"
  | "video_input"
  | "reasoning"
  | "function_call"
  | "native_web_search"
  | "external_network_access";

export type RouteCapabilities = Record<CapabilityName, RouteCapability>;

export interface RouteCapabilityItem {
  route_fingerprint: string;
  provider: string;
  api_type: string;
  model: string;
  media_protocol: string;
  capabilities: RouteCapabilities;
  probe_status?: "idle" | "queued" | "running" | "finished" | "failed";
  updated_at?: string | null;
}

export type TraceOutcome = "ok" | "silent" | "failed" | "unknown" | "partial";

export interface TraceListItem {
  trace_id: string;
  started_at: string;
  finished_at: string | null;
  session_type: string;
  group_id: string | null;
  user_id: string;
  user_name: string;
  avatar_url: string | null;
  outcome: TraceOutcome;
  diagnosis_code: string;
  input_summary: string;
  elapsed_ms: number | null;
}

export interface TraceStage {
  key: string;
  label: string;
  status: "pending" | "running" | "ok" | "warn" | "error" | "skipped" | "unknown";
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  summary: string;
  detail_code: string;
  remaining_ms: number | null;
}

export interface TraceToolStep {
  name: string;
  namespace: string;
  status: string;
  duration_ms: number | null;
  argument_summary: string;
  result_summary: string;
  schema_hash: string;
  detail_code: string;
}

export interface TraceDecision {
  summary: string;
  action: string;
  tier: number | null;
  wait_seconds: number | null;
  interest: number | null;
  reason_code: string;
}

export interface TraceDetail extends TraceListItem {
  bot_id: string;
  media_summary: string[];
  decision: TraceDecision;
  stages: TraceStage[];
  tools: TraceToolStep[];
  final_reply: string;
  send_status: string;
  history_status: string;
  recovery_ids: number[];
}

export type RecoveryStatus =
  | "pending"
  | "processing"
  | "recovered"
  | "quarantined"
  | "expired"
  | "exhausted"
  | "abandoned";

export interface RecoveryItem {
  id: number;
  bot_id: string;
  session_type: string;
  session_id: string;
  message_id: string;
  safe_summary: string;
  failure_class: string;
  failure_stage: string;
  status: RecoveryStatus;
  attempts: number;
  first_failed_at: string;
  last_failed_at: string;
  expires_at: string;
  trace_id: string;
  outcome_unknown: boolean;
  missing_segments: number[];
}

export interface OverviewSnapshot {
  generated_at: string;
  runtime_status: "healthy" | "degraded" | "offline" | "unknown";
  active_turns: number;
  events_last_hour: number;
  p95_turn_ms: number | null;
  route_counts: Record<CapabilityState, number>;
  recovery_counts: Record<string, number>;
  latest_traces: TraceListItem[];
  diagnostics: Array<{
    code: string;
    title: string;
    level: "info" | "warn" | "error";
    trace_id?: string;
  }>;
}

export interface RuntimeEvent {
  id: number;
  ts: string | number;
  topic: string;
  trace_id?: string;
  payload: Record<string, unknown>;
}

export interface OperationStep {
  key: string;
  label: string;
  status: "pending" | "ok" | "warn" | "error" | "skipped" | "unknown";
  message?: string;
}

export interface OperationDiagnostic {
  ok: boolean;
  code: string;
  phase: string;
  title: string;
  message: string;
  retryable: boolean;
  partial: boolean;
  outcome_unknown: boolean;
  operation_id?: string;
  trace_id?: string;
  warnings: string[];
  suggestion?: string;
  steps: OperationStep[];
}
