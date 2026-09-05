import { api, API_BASE, rawApiRequest } from "./client";
import type {
  AgentRuntimeSnapshot,
  BotIdentity,
  ConfigPage,
  ConfigPatchResult,
  ConfigMetadata,
  FunctionalTestRun,
  HealthCatalog,
  OperationDiagnostic,
  OverviewSnapshot,
  Page,
  PersonaListItem,
  GroupListItem,
  GroupSwitchPage,
  StickerPage,
  CatalogItem,
  CapabilityName,
  CursorPage,
  MultimodalRouteSnapshot,
  RecoveryItem,
  RouteCapabilityItem,
  TraceDetail,
  TraceListItem,
  TokenSummary,
  ProactiveNextEligible,
  ProactiveRecord,
  ProactiveStats,
  PluginUpdateOperation,
  PluginUpdateStatus,
  GroupPeerBotBusinessState,
  GroupMemberOption,
  GroupQzoneAgentSettings,
  GroupQzoneAgentState,
  PeerBotCommandTemplate,
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
  return value === "ok" ||
    value === "silent" ||
    value === "no_reply" ||
    value === "finished" ||
    value === "failed" ||
    value === "partial"
    ? value
    : "unknown";
}

export function sanitizeTraceListItem(raw: unknown): TraceListItem {
  const source = isRecord(raw) ? raw : {};
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
  };
}

export function sanitizeTraceDetail(raw: unknown): TraceDetail {
  const source = isRecord(raw) ? raw : {};
  const summary = sanitizeTraceListItem(source);
  const decision = isRecord(source.decision) ? source.decision : {};
  const rawStages = Array.isArray(source.stages) ? source.stages : [];
  const rawTools = Array.isArray(source.tools) ? source.tools : [];

  return {
    ...summary,
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

export function sanitizeTracePage(raw: unknown): Page<TraceListItem> {
  const source = isRecord(raw) ? raw : {};
  const page = finiteNumber(source.page);
  const pageSize = finiteNumber(source.page_size);
  const total = finiteNumber(source.total);
  const totalPages = finiteNumber(source.total_pages);
  return {
    items: Array.isArray(source.items) ? source.items.map(sanitizeTraceListItem) : [],
    page: page !== null && page >= 1 ? Math.trunc(page) : 1,
    page_size: pageSize !== null && pageSize >= 1 ? Math.trunc(pageSize) : 20,
    total: total !== null && total >= 0 ? Math.trunc(total) : 0,
    total_pages: totalPages !== null && totalPages >= 0 ? Math.trunc(totalPages) : 0,
  };
}

export const resources = {
  adminIdentity(signal?: AbortSignal): Promise<{ qq: string; device_id: string; label: string; identity_source: "SUPERUSER" | "plugin_admin" }> {
    return api.get("/admin-identity", undefined, signal);
  },
  bots(signal?: AbortSignal): Promise<{ items: BotIdentity[]; total: number; diagnostic_code: string }> {
    return api.get("/bots", undefined, signal);
  },
  metrics(window: "24h" | "7d" | "30d" | "all", botId = "", signal?: AbortSignal): Promise<TokenSummary> {
    return api.get("/metrics/summary", { window, bot_id: botId }, signal);
  },
  subscriptionQuotas(force = false, signal?: AbortSignal): Promise<import("./types").SubscriptionQuotaResponse> {
    return api.get("/metrics/subscription-quotas", { force }, signal);
  },
  agentRuntime(botId = "", signal?: AbortSignal): Promise<AgentRuntimeSnapshot> {
    return api.get("/runtime/agent", { bot_id: botId }, signal);
  },
  health(signal?: AbortSignal): Promise<HealthCatalog> {
    return api.get("/health", undefined, signal);
  },
  prepareTestRun(testId: string, targetSummary = "", routeFingerprint = ""): Promise<FunctionalTestRun> {
    return api.post("/test-runs/prepare", { test_id: testId, target_summary: targetSummary, route_fingerprint: routeFingerprint });
  },
  confirmTestRun(id: string, targetConfirmation = ""): Promise<FunctionalTestRun> {
    return api.post(`/test-runs/${encodeURIComponent(id)}/confirm`, { confirmed: true, target_confirmation: targetConfirmation });
  },
  testRun(id: string, signal?: AbortSignal): Promise<FunctionalTestRun> {
    return api.get(`/test-runs/${encodeURIComponent(id)}`, undefined, signal);
  },
  prepareSafeTestBatch(): Promise<import("./types").FunctionalTestBatch> {
    return api.post("/test-batches/prepare", { profile: "safe_full" });
  },
  confirmSafeTestBatch(id: string): Promise<import("./types").FunctionalTestBatch> {
    return api.post(`/test-batches/${encodeURIComponent(id)}/confirm`, { confirmed: true });
  },
  testBatch(id: string, signal?: AbortSignal): Promise<import("./types").FunctionalTestBatch> {
    return api.get(`/test-batches/${encodeURIComponent(id)}`, undefined, signal);
  },
  cancelTestBatch(id: string): Promise<import("./types").FunctionalTestBatch> {
    return api.delete(`/test-batches/${encodeURIComponent(id)}`);
  },
  overview(signal?: AbortSignal): Promise<OverviewSnapshot> {
    return api.get("/overview", undefined, signal);
  },
  routes(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<RouteCapabilityItem>> {
    return api.get("/routes/capabilities", { page, page_size: pageSize, search }, signal);
  },
  queueRouteProbe(
    routeFingerprint: string,
    capability: CapabilityName,
    confirmed = true,
    sampleMode: "builtin" | "upload" = "builtin",
    sampleId = "",
  ): Promise<OperationDiagnostic> {
    return api.post(`/routes/capabilities/${encodeURIComponent(routeFingerprint)}/probes`, {
      capability,
      confirmed,
      sample_mode: sampleMode,
      ...(sampleId ? { sample_id: sampleId } : {}),
    });
  },
  uploadRouteMediaProbe(
    routeFingerprint: string,
    capability: Extract<CapabilityName, "audio_input" | "video_input">,
    file: File,
    signal?: AbortSignal,
  ): Promise<OperationDiagnostic> {
    const query = new URLSearchParams({ capability, confirmed: "true" });
    return rawApiRequest<OperationDiagnostic>(
      API_BASE,
      `/routes/capabilities/${encodeURIComponent(routeFingerprint)}/probes/media?${query.toString()}`,
      file,
      {
        "Content-Type": file.type || "application/octet-stream",
        "X-Personification-Media-Filename": file.name,
      },
      signal,
    );
  },
  async traces(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<TraceListItem>> {
    const raw = await api.get<unknown>("/traces", { page, page_size: pageSize, search }, signal);
    return sanitizeTracePage(raw);
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
  personasFiltered(page: number, pageSize: number, filters: { search?: string; group_id?: string; favorability_level?: string; sort_by?: string; direction?: string }, signal?: AbortSignal): Promise<Page<PersonaListItem>> {
    return api.get("/personas", { page, page_size: pageSize, ...filters }, signal);
  },
  groups(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<Page<GroupListItem>> {
    return api.get("/groups", { page, page_size: pageSize, search }, signal);
  },
  groupsFiltered(page: number, pageSize: number, filters: { search?: string; membership_state?: string; include_unconfirmed?: boolean; enabled?: string; bot_id?: string; sort_by?: string; direction?: string }, signal?: AbortSignal): Promise<Page<GroupListItem>> {
    return api.get("/groups", { page, page_size: pageSize, ...filters }, signal);
  },
  groupSwitches(page: number, pageSize: number, filters: { search?: string; enabled?: string; membership_state?: string; bot_id?: string }, signal?: AbortSignal): Promise<GroupSwitchPage> {
    return api.get("/group-switches", { page, page_size: pageSize, ...filters }, signal);
  },
  updateGroupSwitch(groupId: string, enabled: boolean): Promise<OperationDiagnostic> {
    return api.post(`/group-switches/${encodeURIComponent(groupId)}`, { enabled });
  },
  proactiveStats(scope = "", signal?: AbortSignal): Promise<ProactiveStats> {
    return api.get("/proactive/stats", { scope, since_hours: 72 }, signal);
  },
  proactiveRecent(filters: { scope?: string; outcome?: string; target?: string; cursor?: number; limit?: number }, signal?: AbortSignal): Promise<CursorPage<ProactiveRecord>> {
    return api.get("/proactive/recent", filters, signal);
  },
  proactiveNextEligible(scope = "", signal?: AbortSignal): Promise<{ items: ProactiveNextEligible[]; total: number; diagnostic_code: string }> {
    return api.get("/proactive/next-eligible", { scope }, signal);
  },
  stickers(page = 1, pageSize = 20, search = "", signal?: AbortSignal): Promise<StickerPage> {
    return api.get("/stickers", { page, page_size: pageSize, search }, signal);
  },
  rebuildStickerIndex(): Promise<OperationDiagnostic> {
    return api.post("/stickers/index/rebuild");
  },
  config(page = 1, pageSize = 20, filters: { search?: string; group?: string; modified?: boolean; restart_required?: boolean; hot_reloadable?: boolean; advanced?: boolean; secret?: boolean; invalid?: boolean } = {}, signal?: AbortSignal): Promise<ConfigPage> {
    return api.get("/config", { page, page_size: pageSize, ...filters }, signal);
  },
  configMetadata(signal?: AbortSignal): Promise<ConfigMetadata> {
    return api.get("/config/meta", undefined, signal);
  },
  patchConfig(revision: string, values: Record<string, unknown>): Promise<ConfigPatchResult> {
    return api.patch("/config/values", { revision, values });
  },
  searchEngineSpeedTest(): Promise<OperationDiagnostic> {
    return api.post("/config-tools/search-engines/speed-test", {});
  },
  applyRecommendedConfig(): Promise<OperationDiagnostic> {
    return api.post("/config-tools/apply-recommended", {});
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
  qzoneCapabilities(signal?: AbortSignal, botId = ""): Promise<Record<string, unknown>> {
    return api.get("/qzone/capabilities", { bot_id: botId }, signal);
  },
  videoRouteProbe(file: File, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.upload("/tests/video-route", file, signal);
  },
  videoTurnTest(file: File, text = "", signal?: AbortSignal): Promise<Record<string, unknown>> {
    const query = text ? `?text=${encodeURIComponent(text)}` : "";
    return api.upload(`/tests/video-turn${query}`, file, signal);
  },
  mediaTurnBuiltin(mediaKind: "audio" | "video", sampleId = "", text = ""): Promise<Record<string, unknown>> {
    return api.post("/tests/media-turn/builtin", {
      media_kind: mediaKind,
      ...(sampleId ? { sample_id: sampleId } : {}),
      ...(text ? { text } : {}),
    });
  },
  mediaTurnUpload(mediaKind: "audio" | "video", file: File, text = "", signal?: AbortSignal): Promise<Record<string, unknown>> {
    const query = new URLSearchParams({ media_kind: mediaKind });
    if (text) query.set("text", text);
    return rawApiRequest<Record<string, unknown>>(
      API_BASE,
      `/tests/media-turn/upload?${query.toString()}`,
      file,
      {
        "Content-Type": file.type || "application/octet-stream",
        "X-Personification-Media-Filename": file.name,
      },
      signal,
    );
  },
  personaPromptPreview(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/model-tests/persona-prompt", undefined, signal);
  },
  modelChat(mode: "single" | "all", prompt: string): Promise<Record<string, unknown>> {
    return api.post(mode === "single" ? "/model-tests/chat" : "/model-tests/chat-all", { prompt, system: "你是管理台连通性测试助手，请简洁回复。" });
  },
  personaDetail(userId: string, groupId = "", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/persona-details/${encodeURIComponent(userId)}`, { group_id: groupId }, signal);
  },
  refreshPersona(userId: string, groupId: string, botId: string): Promise<Record<string, unknown>> {
    return api.post(`/persona-details/${encodeURIComponent(userId)}/group-refresh`, { group_id: groupId, bot_id: botId });
  },
  correctPersona(userId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/persona-details/${encodeURIComponent(userId)}/correction`, body);
  },
  refreshPersonaAvatar(userId: string): Promise<Record<string, unknown>> {
    return api.post(`/persona-details/${encodeURIComponent(userId)}/avatar-analysis/refresh`, {});
  },
  clearPersonaAvatar(userId: string): Promise<Record<string, unknown>> {
    return api.delete(`/persona-details/${encodeURIComponent(userId)}/avatar-analysis`);
  },
  groupBusiness(groupId: string, section: "personas" | "aliases" | "schedule" | "style" | "agent-state" | "knowledge" | "memes", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/group-management/${encodeURIComponent(groupId)}/${section}`, section === "personas" ? { page: 1, page_size: 20 } : undefined, signal);
  },
  groupPersonas(groupId: string, page = 1, search = "", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/group-management/${encodeURIComponent(groupId)}/personas`, { page, page_size: 20, search }, signal);
  },
  groupPeerBots(groupId: string, signal?: AbortSignal): Promise<GroupPeerBotBusinessState> {
    return api.get(`/group-management/${encodeURIComponent(groupId)}/peer-bots`, undefined, signal);
  },
  updateGroupPeerBotSettings(groupId: string, body: { enabled?: boolean; max_calls_per_turn?: 1; cooldown_seconds?: number; pending_ttl_seconds?: number; max_chain_depth?: 1; auto_learn_approved_commands?: boolean }): Promise<OperationDiagnostic> {
    return api.put(`/group-management/${encodeURIComponent(groupId)}/peer-bots/settings`, body);
  },
  updateGroupPeerBotStatus(groupId: string, userId: string, action: "approve" | "reject" | "clear", nickname = ""): Promise<OperationDiagnostic> {
    return api.put(`/group-management/${encodeURIComponent(groupId)}/peer-bots/${encodeURIComponent(userId)}`, { action, nickname });
  },
  saveGroupPeerBotCommand(groupId: string, userId: string, commandId: string, command: Pick<PeerBotCommandTemplate, "full_template" | "parameter_schema" | "risk_level" | "status"> & Partial<Pick<PeerBotCommandTemplate, "command_entry" | "subcommands" | "argument_template" | "description">>): Promise<OperationDiagnostic> {
    return api.put(`/group-management/${encodeURIComponent(groupId)}/peer-bots/${encodeURIComponent(userId)}/commands/${encodeURIComponent(commandId)}`, command);
  },
  deleteGroupPeerBotCommand(groupId: string, userId: string, commandId: string): Promise<OperationDiagnostic> {
    return api.delete(`/group-management/${encodeURIComponent(groupId)}/peer-bots/${encodeURIComponent(userId)}/commands/${encodeURIComponent(commandId)}`);
  },
  discoverGroupPeerBots(groupId: string): Promise<OperationDiagnostic> {
    return api.post(`/group-management/${encodeURIComponent(groupId)}/peer-bots/discover`, {});
  },
  resetGroupPeerBotLoop(groupId: string): Promise<OperationDiagnostic> {
    return api.post(`/group-management/${encodeURIComponent(groupId)}/peer-bots/reset-loop`, {});
  },
  groupMembers(groupId: string, botId: string, signal?: AbortSignal, offset = 0, search = ""): Promise<{ members: GroupMemberOption[]; total: number; has_more?: boolean }> {
    return api.get(`/qq-management/groups/${encodeURIComponent(groupId)}/members`, { bot_id: botId, limit: 50, offset, search }, signal);
  },
  groupQzoneAgent(groupId: string, signal?: AbortSignal): Promise<GroupQzoneAgentState> {
    return api.get(`/group-management/${encodeURIComponent(groupId)}/qzone-agent`, undefined, signal);
  },
  updateGroupQzoneAgent(groupId: string, body: Partial<GroupQzoneAgentSettings>): Promise<OperationDiagnostic> {
    return api.put(`/group-management/${encodeURIComponent(groupId)}/qzone-agent`, body);
  },
  rebuildGroup(groupId: string, kind: "style" | "knowledge"): Promise<Record<string, unknown>> {
    return api.post(`/group-management/${encodeURIComponent(groupId)}/${kind}/rebuild`, { confirm: true });
  },
  saveGroupAliases(groupId: string, userId: string, aliases: string, note = ""): Promise<Record<string, unknown>> {
    return api.put(`/group-management/${encodeURIComponent(groupId)}/aliases/${encodeURIComponent(userId)}`, { alias_text: aliases, note });
  },
  deleteGroupAliases(groupId: string, userId: string): Promise<Record<string, unknown>> {
    return api.delete(`/group-management/${encodeURIComponent(groupId)}/aliases/${encodeURIComponent(userId)}`);
  },
  saveGroupSchedule(groupId: string, enabled: boolean, schedulePrompt: string): Promise<Record<string, unknown>> {
    return api.put(`/group-management/${encodeURIComponent(groupId)}/schedule`, { enabled, schedule_prompt: schedulePrompt });
  },
  generateGroupSchedule(groupId: string, personaHint = ""): Promise<Record<string, unknown>> {
    return api.post(`/group-management/${encodeURIComponent(groupId)}/schedule/auto-generate`, { persona_hint: personaHint });
  },
  saveGroupMeme(groupId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/group-management/${encodeURIComponent(groupId)}/memes`, body);
  },
  deleteGroupMeme(groupId: string, term: string): Promise<Record<string, unknown>> {
    return api.delete(`/group-management/${encodeURIComponent(groupId)}/memes/${encodeURIComponent(term)}`);
  },
  updateSticker(name: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.patch(`/sticker-management/${encodeURIComponent(name)}`, body);
  },
  deleteSticker(name: string): Promise<Record<string, unknown>> {
    return api.delete(`/sticker-management/${encodeURIComponent(name)}`);
  },
  uploadSticker(file: File, description = ""): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.set("file", file, file.name);
    form.set("description", description);
    return api.post("/sticker-management/upload", form);
  },
  rescanStickers(): Promise<Record<string, unknown>> {
    return api.post("/sticker-management/rescan", { confirm: true });
  },
  qzoneGet(path: string, query: Record<string, string | number | boolean> = {}, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/qzone-management/${path.replace(/^\/+/, "")}`, query, signal);
  },
  qzonePost(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.post(`/qzone-management/${path.replace(/^\/+/, "")}`, body);
  },
  memoryBusiness(section: "recent" | "inner-state" | "graph" | "palace-zones" | "vector-index", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/memory/${section}`, undefined, signal);
  },
  memorySearch(query: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/memory/search-test", { query }, signal);
  },
  rebuildMemoryIndex(): Promise<Record<string, unknown>> {
    return api.post("/memory/vector-index/rebuild", { confirm: true });
  },
  skillAction(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.post(`/skill-management/${path.replace(/^\/+/, "")}`, body);
  },
  mcpGet(path: string, query: Record<string, string | number | boolean> = {}, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/mcp-management/${path.replace(/^\/+/, "")}`, query, signal);
  },
  mcpPost(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.post(`/mcp-management/${path.replace(/^\/+/, "")}`, body);
  },
  toolCreatorGet(path = "tasks", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/tool-creator/${path.replace(/^\/+/, "")}`, undefined, signal);
  },
  toolCreatorPost(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.post(`/tool-creator/${path.replace(/^\/+/, "")}`, body);
  },
  pluginKnowledgeDetail(name: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/plugin-knowledge-management/detail/${encodeURIComponent(name)}`, undefined, signal);
  },
  pluginKnowledgeSearch(query: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/plugin-knowledge-management/search", { q: query }, signal);
  },
  pluginKnowledgeStatus(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/plugin-knowledge-management/status", undefined, signal);
  },
  pluginKnowledgeSection(name: string, section: string, page = 1, pageSize = 20, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/plugin-knowledge-management/detail/${encodeURIComponent(name)}/sections/${encodeURIComponent(section)}`, { page, page_size: pageSize }, signal);
  },
  startPluginKnowledgeBuild(): Promise<Record<string, unknown>> {
    return api.post("/plugin-knowledge-management/builds", { mode: "one_shot", confirmed: true });
  },
  pluginKnowledgeBuild(id: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/plugin-knowledge-management/builds/${encodeURIComponent(id)}`, undefined, signal);
  },
  cancelPluginKnowledgeBuild(id: string): Promise<Record<string, unknown>> {
    return api.delete(`/plugin-knowledge-management/builds/${encodeURIComponent(id)}`);
  },
  personaBuilderGet(path: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/persona-builder/${path.replace(/^\/+/, "")}`, undefined, signal);
  },
  personaBuilderPost(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.post(`/persona-builder/${path.replace(/^\/+/, "")}`, body);
  },
  pluginUpdateStatus(signal?: AbortSignal): Promise<PluginUpdateStatus> {
    return api.get("/plugin-update/status", undefined, signal);
  },
  pluginUpdateBenchmark(): Promise<{ operation: PluginUpdateOperation; status: PluginUpdateStatus }> {
    return api.post("/plugin-update/benchmark", {});
  },
  pluginUpdateCheck(): Promise<{ operation: PluginUpdateOperation; status: PluginUpdateStatus }> {
    return api.post("/plugin-update/check", {});
  },
  pluginUpdateApply(): Promise<{ ok: boolean; updated: boolean; operation: PluginUpdateOperation; status: PluginUpdateStatus; message?: string; error?: string }> {
    return api.post("/plugin-update/apply", { confirmation: "UPDATE" });
  },
  pluginUpdateHistory(signal?: AbortSignal): Promise<{ items: PluginUpdateOperation[]; total: number }> {
    return api.get("/plugin-update/history", undefined, signal);
  },
  userPolicyStates(tier = "", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/user-policies/states", { tier, limit: 100 }, signal);
  },
  userPolicyEvents(userId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/user-policies/${encodeURIComponent(userId)}/events`, { include_evidence: true, limit: 100 }, signal);
  },
  updateUserPolicy(userId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/user-policies/${encodeURIComponent(userId)}/override`, body);
  },
  outboundRecent(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/outbound/recent", { limit: 100 }, signal);
  },
  recallOutbound(operationId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/outbound/${encodeURIComponent(operationId)}/recall`, body);
  },
  auditRecent(action = "", signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/audit/recent", { action, limit: 100 }, signal);
  },
  auditActions(signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get("/audit/actions", undefined, signal);
  },
  clearLogs(): Promise<Record<string, unknown>> {
    return api.delete("/log-management/clear", { confirm: "CLEAR LOGS" });
  },
  qqGet(path: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/qq-management/${path.replace(/^\/+/, "")}`, undefined, signal);
  },
  qqPost(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/qq-management/${path.replace(/^\/+/, "")}`, body);
  },
  deviceGet(path: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/device-management/${path.replace(/^\/+/, "")}`, undefined, signal);
  },
  devicePost(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.post(`/device-management/${path.replace(/^\/+/, "")}`, body);
  },
  deviceDelete(path: string, body: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return api.delete(`/device-management/${path.replace(/^\/+/, "")}`, body);
  },
  createStateExport(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post("/data-transfer/exports/create", body);
  },
  uploadStateImport(file: File): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.set("file", file, file.name);
    return api.post("/data-transfer/imports/upload", form);
  },
  inspectImport(taskId: string, signal?: AbortSignal): Promise<Record<string, unknown>> {
    return api.get(`/data-transfer/imports/${encodeURIComponent(taskId)}/inspect`, undefined, signal);
  },
  dryRunImport(taskId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/data-transfer/imports/${encodeURIComponent(taskId)}/dry-run`, body);
  },
  applyImport(taskId: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.post(`/data-transfer/imports/${encodeURIComponent(taskId)}/apply`, body);
  },
  rollbackImport(journalId: string): Promise<Record<string, unknown>> {
    return api.post(`/data-transfer/imports/${encodeURIComponent(journalId)}/rollback`, {});
  },
  qqDelete(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return api.delete(`/qq-management/${path.replace(/^\/+/, "")}`, body);
  },
};
