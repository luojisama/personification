export interface Page<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export type CatalogItem = Record<string, unknown>;

export interface CursorPage<T> {
  items: T[];
  next_cursor: number;
  has_more: boolean;
  limit: number;
  filters: Record<string, unknown>;
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

export interface PersonaListItem {
  qq_id: string;
  user_id: string;
  nickname: string;
  avatar_url: string | null;
  recent_group_id: string;
  favorability_score: number;
  favorability_level: string;
  favorability: { score: number; level: string };
  updated_at: number;
  source: string;
  cache_only: boolean;
}

export interface GroupListItem {
  group_id: string;
  group_name: string;
  avatar_url: string | null;
  enabled: boolean;
  membership_state: "confirmed" | "configured" | "unconfirmed";
  bot_ids: string[];
  sources: string[];
  bot_self_ids: string[];
  member_count: number | null;
  last_active_at: number | null;
  freshness: number;
  cache_only: boolean;
}

export interface StickerListItem {
  filename: string;
  size_bytes: number;
  modified_at: number;
  thumbnail_url: string;
  description: string;
  mood_tags: string[];
  scene_tags: string[];
  labeled: boolean;
}

export interface StickerPage extends Page<StickerListItem> {
  index_status: string;
  index_detail_code: string;
  index_updated_at: number;
  index_stale: boolean;
}

export interface ConfigListItem {
  key: string;
  field_name: string;
  display_name: string;
  description: string;
  group: string;
  category: string;
  scope: string;
  kind: string;
  value_type: string;
  value: unknown;
  default: unknown;
  secret: boolean;
  advanced: boolean;
  hot_reloadable: boolean;
  restart_required: boolean;
  required: boolean;
  modified: boolean;
  aliases: string[];
  choices: string[];
  min_value: number | null;
  max_value: number | null;
}

export interface ConfigPage extends Page<ConfigListItem> {
  revision: string;
  groups: string[];
  group_counts: Record<string, number>;
  modified_counts: Record<string, number>;
}

export interface ConfigPatchResult {
  revision: string;
  updated_keys: string[];
  hot_reloaded_keys: string[];
  restart_required_keys: string[];
  warnings: Array<{ code: string; message: string }>;
}

export interface BotIdentity {
  bot_id: string;
  nickname: string;
  avatar_url: string | null;
  online: boolean;
  is_default: boolean;
  last_seen_at: number | string | null;
}

export interface AgentRuntimeSnapshot {
  bot: BotIdentity;
  connected_bots: BotIdentity[];
  enabled: boolean;
  running: boolean;
  last_active_at: number | null;
  waiting_turns: number;
  active_turns: number;
  sending_turns: number;
  gated_turns: number;
  cancelled_turns: number;
  stale_turns: number;
  event_loop_p50_ms: number | null;
  event_loop_p95_ms: number | null;
  turn_p50_ms: number | null;
  turn_p95_ms: number | null;
  rss_bytes: number | null;
  peak_rss_bytes: number | null;
  background_tasks: number;
  background_failures: number;
  cache_entries: number;
  inner_state: { mood: string; energy: string; pending_count: number; updated_at: string };
  recent_traces: Array<{
    trace_id: string;
    state: string;
    outcome: string;
    updated_at: number;
    elapsed_ms: number | null;
    model: string;
    tool_count: number;
    session_type: string;
    group_id: string;
    diagnosis_code: string;
  }>;
  generated_at: number;
}

export interface TokenUsageRow {
  bucket?: string;
  label?: string;
  model?: string;
  provider?: string;
  purpose?: string;
  purpose_label?: string;
  group_id?: string;
  group_label?: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  call_count: number;
  percent?: number;
}

export interface TokenSummary {
  window: "day" | "week" | "month" | "all";
  generated_at: number;
  total: TokenUsageRow;
  series: TokenUsageRow[];
  by_model: TokenUsageRow[];
  by_group: TokenUsageRow[];
  by_purpose: TokenUsageRow[];
  provider_usage: Array<TokenUsageRow & { label: string; monthly_limit: number; usage_ratio: number; unlimited: boolean }>;
  billing: {
    cost_configured: boolean;
    currency: string;
    request_cost: number;
    note: string;
  };
  dashboard_overview?: Record<string, unknown>;
}

export type FunctionalTestRisk = "local_read" | "external_read" | "external_write";
export interface FunctionalTestDefinition {
  id: string;
  label: string;
  category: string;
  risk: FunctionalTestRisk;
}
export interface FunctionalTestRun {
  id: string;
  test_id: string;
  label: string;
  risk: FunctionalTestRisk;
  state: "prepared" | "awaiting_confirmation" | "running" | "succeeded" | "failed" | "unknown";
  target_summary: string | null;
  route_fingerprint: string | null;
  trace_id: string | null;
  diagnostic_code: string;
  created_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  result_summary: Record<string, unknown>;
}

export interface HealthCatalog {
  tests: FunctionalTestDefinition[];
  cached: Record<string, unknown> | null;
  diagnostic_code: string;
}

export interface MultimodalRouteSnapshot {
  audio: {
    enabled: boolean;
    primary_native: boolean;
    route_available: boolean;
    asr_provider: string;
    asr_model: string;
    fallback_order: string[];
  };
  video: {
    enabled: boolean;
    route_mode: string;
    primary_native: boolean;
    gemini_web_enabled: boolean;
    external_fallback_enabled: boolean;
    storyboard_fallback_enabled: boolean;
    fallback_order: string[];
  };
  diagnostic_code: string;
  production_verified: boolean;
  dependencies: {
    ffmpeg: { available: boolean; version: string; diagnostic_code: string };
    ffprobe: { available: boolean; version: string; diagnostic_code: string };
  };
}
