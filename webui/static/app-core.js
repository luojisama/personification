const API = "/personification/api";
const QZONE_OPERATION_STORAGE_KEY = "personification_qzone_operation_id_v1";

function readStoredQzoneOperationId() {
  try { return String(sessionStorage.getItem(QZONE_OPERATION_STORAGE_KEY) || "").trim(); }
  catch { return ""; }
}

let state = {
  logged: false, qq: "", view: "dashboard",
  entries: [], groups: [], activeGroup: null, configSearch: "", configSearchComposing: false, configSearchDraft: "",
  devices: [], devicePending: false, alert: null, loading: false, loadingMessage: "",
  dashboard: null, dashboardWindow: "month", dashboardDetail: null,
  personas: [], selectedPersona: null, personaSearch: "",
  groupList: [], selectedGroup: null, groupPersonas: [], groupStyle: null, groupKnowledge: [], groupAliasDrafts: {}, groupSchedule: null, groupScheduleGenerating: false,
  groupFavorability: null,
  groupSwitches: [], newGroupId: "",
  skills: [], skillFilter: "", skillSummary: null, skillRemoteSources: [], skillMcpTools: [],
  skillSourceForm: { source: "", name: "", ref: "", subdir: "", kind: "auto", preferFirst: false, autoApprove: false },
  toolCreatorTasks: [], toolCreatorSelectedId: "", toolCreatorDetail: null, toolCreatorRequest: "", toolCreatorSuggestedName: "", toolCreatorAnswer: "", toolCreatorBusy: false, toolCreatorDiagnostic: null,
  mcpSources: [], mcpSourceId: "official", mcpQuery: "", mcpResults: [], mcpNextCursor: "", mcpDetail: null, mcpPackageIndex: 0, mcpPrefix: "", mcpInstallations: [], mcpBusy: false,
  testPrompt: "你好，自我介绍一下", testSystem: "你是测试助手，简洁回复。", testResult: null, testAllResult: null,
  personaTemplateForm: { mode: "source", work_title: "", character_name: "", persona_name: "", gender: "", personality: "", traits: "", hobbies: "", description: "" }, personaTemplateResult: null, personaTemplateBusy: false, personaTemplateTask: null, personaTemplateHistory: [],
  personaAvatarCandidateId: "", personaSignatureCandidateId: "", personaProfileBotId: "",
  personaPrompt: null, personaPromptPath: "", health: null, healthBusyCat: "", interactionResult: null, interactionBusy: false,
  qzoneForwardForm: { target_user_id: "", forward_text: "" }, qzoneForwardResult: null, qzoneForwardBusy: false,
  qzoneBusy: false, qzonePostResult: null, qzoneActionBusy: "", qzoneActionResult: null, qzoneOperationId: readStoredQzoneOperationId(), qzoneRecoveredOperation: null, qzoneBotId: "", qzoneLogin: null, qzoneAuthBusy: "", qzoneAuthResult: null,
  qzoneReconcileBusy: "", qzoneCandidateBusy: false, qzoneCandidates: null, qzoneHistoryBusy: "",
  pluginUpdateStatus: null, pluginUpdateHistory: null, pluginUpdateBusy: false, pluginUpdateChecking: false, pluginUpdateResult: null,
  qqInfo: null, qqGroups: [], qqFriends: [],
  memory: null, memoryFilter: "", memoryInnerState: null, memoryIncludeSelf: false, memoryLimit: 200,
  memoryVectorIndex: null, memorySearchQuery: "", memorySearchResult: null, memoryVectorBusy: false,
  memoryGraph: null, memoryGraphGroupId: "", memoryGraphLimit: 100, memoryGraphMinSalience: 0,
  groupRawChat: null, groupStyleSnapIdx: 0, groupStyleRebuilding: false,
  showAdvancedConfig: false,
  stickers: null, stickerSearch: "", selectedSticker: null,
  theme: "dark", mobileNavOpen: false, eligibleAdmins: [],
  audit: null, auditFilter: "",
  logs: null, traces: null, logLevel: "", logQuery: "", logTraceId: "", logLoadingMore: false, logExpandedIds: {}, traceDetail: null, selectedTraceId: "",
  proactiveStats: null, proactiveRecent: null, proactiveScope: "",
  agentStatus: null, transferExport: null, transferImport: null, transferBotInfo: null,
};

const VIEW_ASSETS = {
  dashboard:"app-admin.js",health:"app-admin.js",qzone:"app-admin.js",personas:"app-admin.js",groups:"app-admin.js",group_switch:"app-admin.js",persona_prompt:"app-admin.js",persona_builder:"app-admin.js",qq:"app-admin.js",
  config:"app-config.js",memory:"app-content.js",memory_graph:"app-content.js",stickers:"app-content.js",
  skills:"app-tools.js",tool_creator:"app-tool-creator.js",plugin_knowledge:"app-tools.js",plugin_manager:"app-tools.js",test:"app-tools.js",
  proactive:"app-activity.js",audit:"app-activity.js",logs:"app-activity.js",traces:"app-activity.js",trace_detail:"app-activity.js",
  agent_status:"app-operations.js",data_transfer:"app-operations.js",
};
const _loadedAssets = new Set(["app-core.js","app-auth.js"]);
const _assetInflight = new Map();
let _viewAbortController = null;
let _navigationId = 0;
const _SCROLL_STORAGE_KEY = "personification_webui_scroll_v1";
let _scrollPersistFrame = 0;
let _scrollPositions = {views:{}, sidebar:0};
try {
  const saved = JSON.parse(sessionStorage.getItem(_SCROLL_STORAGE_KEY) || "{}");
  if (saved && typeof saved === "object") _scrollPositions = {views:saved.views || {}, sidebar:Number(saved.sidebar || 0)};
} catch {}

function normalizeView(view) {
  const candidate = String(view || "").trim();
  return candidate === "devices" || Object.prototype.hasOwnProperty.call(VIEW_ASSETS, candidate) ? candidate : "dashboard";
}

function persistScrollPositions() {
  try { sessionStorage.setItem(_SCROLL_STORAGE_KEY, JSON.stringify(_scrollPositions)); } catch {}
}

function captureScrollState() {
  if (_scrollPersistFrame) {
    cancelAnimationFrame(_scrollPersistFrame);
    _scrollPersistFrame = 0;
  }
  const main = document.querySelector(".layout > main");
  const nav = document.querySelector("#console-sidebar nav");
  if (main && main.dataset.loading !== "true") {
    const renderedView = normalizeView(main.dataset.view);
    _scrollPositions.views[renderedView] = Math.max(0, Math.round(main.scrollTop || 0));
  }
  if (nav) _scrollPositions.sidebar = Math.max(0, Math.round(nav.scrollTop || 0));
  persistScrollPositions();
}

function restoreScrollState() {
  const renderedView = normalizeView(state.view);
  const mainScrollTop = Math.max(0, Number(_scrollPositions.views[renderedView] || 0));
  const sidebarScrollTop = Math.max(0, Number(_scrollPositions.sidebar || 0));
  requestAnimationFrame(() => {
    const main = document.querySelector(".layout > main");
    const nav = document.querySelector("#console-sidebar nav");
    if (main && main.dataset.view === renderedView && main.dataset.loading !== "true") main.scrollTop = mainScrollTop;
    if (nav) nav.scrollTop = sidebarScrollTop;
  });
}

function queueScrollStateCapture() {
  if (_scrollPersistFrame) return;
  _scrollPersistFrame = requestAnimationFrame(() => {
    _scrollPersistFrame = 0;
    captureScrollState();
  });
}

function ensureViewAsset(view) {
  const filename=VIEW_ASSETS[view];
  if(!filename||_loadedAssets.has(filename))return Promise.resolve();
  if(_assetInflight.has(filename))return _assetInflight.get(filename);
  const promise=new Promise((resolve,reject)=>{const script=document.createElement("script");const version=(window.PERSONIFICATION_ASSET_VERSIONS||{})[filename]||"";script.src=`/personification/static/${filename}${version?`?v=${encodeURIComponent(version)}`:""}`;script.onload=()=>{_loadedAssets.add(filename);resolve();};script.onerror=()=>reject(new Error(`页面资源加载失败：${filename}`));document.head.appendChild(script);});
  _assetInflight.set(filename,promise);
  promise.finally(()=>_assetInflight.delete(filename));
  return promise;
}

function safeHttpUrl(value) { try { const url=new URL(String(value||""),location.origin); return /^(https?):$/.test(url.protocol)?url.href:""; } catch { return ""; } }

function readCookie(name) {
  const items = (document.cookie || "").split("; ");
  for (const it of items) {
    if (it.startsWith(name + "=")) {
      return decodeURIComponent(it.slice(name.length + 1));
    }
  }
  return "";
}

const _apiInflight = new Map();

class ApiError extends Error {
  constructor(diagnostic, status=0, path="") {
    const info = diagnostic && typeof diagnostic === "object" ? diagnostic : {};
    super(String(info.message || info.title || "请求失败"));
    this.name = "ApiError";
    this.status = Number(status || 0);
    this.path = String(path || "");
    this.diagnostic = info;
    this.code = String(info.code || "request_failed");
    this.phase = String(info.phase || "request");
  }
}

function normalizeApiDiagnostic(payload, status=0) {
  const root = payload && typeof payload === "object" ? payload : {};
  const raw = root.detail && typeof root.detail === "object" && !Array.isArray(root.detail)
    ? root.detail : (root.diagnostic && typeof root.diagnostic === "object" ? root.diagnostic : root);
  const validation = Array.isArray(root.detail) ? root.detail : [];
  const message = typeof root.detail === "string" ? root.detail
    : String(raw.message || raw.error || (validation.length ? "请求参数未通过校验" : root.message || "请求失败"));
  const details = Array.isArray(raw.details) ? raw.details.slice() : [];
  for (const item of validation.slice(0, 8)) {
    const where = Array.isArray(item.loc) ? item.loc.join(".") : "请求参数";
    details.push({label:where,value:String(item.msg || "参数无效"),status:"error"});
  }
  return {
    ok:false,
    code:String(raw.code || (status === 401 ? "not_authenticated" : status === 403 ? "permission_denied" : status === 404 ? "not_found" : status === 429 ? "rate_limited" : "request_failed")),
    phase:String(raw.phase || "request"),
    title:String(raw.title || (status ? `请求未完成 · HTTP ${status}` : "请求未完成")),
    message,
    details,
    steps:Array.isArray(raw.steps) ? raw.steps : [],
    warnings:Array.isArray(raw.warnings) ? raw.warnings : [],
    suggestion:String(raw.suggestion || ""),
    retryable:Boolean(raw.retryable),
    partial:Boolean(raw.partial),
    outcome_unknown:Boolean(raw.outcome_unknown),
    operation_id:String(raw.operation_id || ""),
    trace_id:String(raw.trace_id || ""),
  };
}

function operationDiagnosticFromError(error, fallbackTitle="操作未完成") {
  if (error && error.diagnostic) return {...error.diagnostic,title:error.diagnostic.title||fallbackTitle};
  return normalizeApiDiagnostic({title:fallbackTitle,message:String(error&&error.message||error||"未知错误")}, Number(error&&error.status||0));
}

function _diagnosticValue(value) {
  if (value == null || value === "") return "—";
  if (typeof value === "object") { try { return JSON.stringify(value); } catch { return String(value); } }
  return String(value);
}

function operationDiagnosticFingerprint(input) {
  const d = input && input.diagnostic && typeof input.diagnostic === "object" ? input.diagnostic : input;
  if (!d || typeof d !== "object") return "";
  if (d.operation_id) return `operation:${d.operation_id}`;
  if (d.trace_id) return `trace:${d.trace_id}`;
  let details = "";
  try { details = JSON.stringify(d.details || []); } catch {}
  return [d.code, d.phase, d.title, d.message, details].map(value => String(value || "")).join("|");
}

let _operationAnnouncement = "";

function queueOperationAnnouncement(diagnostic) {
  if (!diagnostic || typeof diagnostic !== "object") return;
  _operationAnnouncement = [
    diagnostic.title || (diagnostic.ok === true ? "操作完成" : "操作未完成"),
    diagnostic.phase ? `阶段 ${diagnostic.phase}` : "",
    diagnostic.code ? `代码 ${diagnostic.code}` : "",
  ].filter(Boolean).join("，");
}

function flushOperationAnnouncement() {
  if (!_operationAnnouncement) return;
  const region = document.getElementById("operation-live-region");
  if (!region) return;
  region.textContent = _operationAnnouncement;
  _operationAnnouncement = "";
}

function renderOperationHistory(inputs, options={}) {
  const seen = new Set();
  const excluded = new Set((options.exclude || []).map(operationDiagnosticFingerprint).filter(Boolean));
  const items = [];
  for (const input of Array.isArray(inputs) ? inputs : []) {
    const fingerprint = operationDiagnosticFingerprint(input);
    if (!fingerprint || seen.has(fingerprint) || excluded.has(fingerprint)) continue;
    seen.add(fingerprint);
    items.push(input);
  }
  const group = String(options.group || `view-${state.view || "global"}`);
  return items.map((item, index) => renderOperationDiagnostic(item, {group, expanded:index === 0})).join("");
}

function renderOperationDiagnostic(input, options={}) {
  if (!input) return "";
  const d = input.diagnostic && typeof input.diagnostic === "object" ? input.diagnostic : input;
  const ok = d.ok === true;
  const unknown = Boolean(d.outcome_unknown);
  const tone = unknown ? "unknown" : (ok ? "ok" : (d.partial ? "warn" : "error"));
  const details = Array.isArray(d.details) ? d.details : [];
  const steps = Array.isArray(d.steps) ? d.steps : [];
  const warnings = Array.isArray(d.warnings) ? d.warnings : [];
  const group = String(options.group || `view-${state.view || "global"}`);
  const expanded = options.expanded !== false;
  const detailRows = details.map(item => `<div class="operation-detail ${escapeAttr(item.status||'info')}"><span>${escapeHtml(item.label||'详情')}</span><strong>${escapeHtml(_diagnosticValue(item.value))}</strong></div>`).join("");
  const stepRows = steps.map((item,index) => `<li class="operation-step ${escapeAttr(item.status||'unknown')}"><span class="operation-step-index">${String(index+1).padStart(2,'0')}</span><div><strong>${escapeHtml(item.label||item.key||'步骤')}</strong>${item.message?`<p>${escapeHtml(item.message)}</p>`:''}${Array.isArray(item.details)&&item.details.length?`<div class="operation-step-details">${item.details.map(child=>`<span>${escapeHtml(child.label||'详情')}：${escapeHtml(_diagnosticValue(child.value))}</span>`).join('')}</div>`:''}</div><em>${escapeHtml(item.status||'unknown')}</em></li>`).join("");
  const trace = d.trace_id ? `<button class="btn small operation-trace-button" data-operation-trace="${escapeAttr(d.trace_id)}">查看 Trace</button>` : "";
  const retryLabel = unknown ? "禁止直接重试" : (d.retryable ? "可以重试" : "不要直接重试");
  queueOperationAnnouncement(d);
  return `<details class="operation-diagnostic ${tone}" data-operation-group="${escapeAttr(group)}" ${expanded?'open':''}>
    <summary class="operation-summary"><span class="operation-summary-mark">${renderIcon(unknown?'alert-triangle':ok?'check':'alert-circle','operation-status-icon')}</span><span class="operation-summary-copy"><span class="eyebrow">OPERATION DIAGNOSTIC</span><strong>${escapeHtml(d.title||(ok?'操作完成':'操作未完成'))}</strong><small>PHASE / ${escapeHtml(d.phase||'未标记')}</small></span><code class="operation-code">${escapeHtml(d.code||(ok?'ok':'operation_failed'))}</code><span class="operation-chevron">${renderIcon('chevron-down','ui-icon')}</span></summary>
    <div class="operation-diagnostic-body">
      <header><p>${escapeHtml(d.message||'未提供说明')}</p></header>
      <div class="operation-meta"><span>阶段 <strong>${escapeHtml(d.phase||'未标记')}</strong></span><span>重试策略 <strong>${retryLabel}</strong></span>${d.partial?'<span><strong>部分完成</strong></span>':''}${unknown?'<span><strong>远端结果未知</strong></span>':''}</div>
      ${detailRows?`<div class="operation-details">${detailRows}</div>`:''}
      ${stepRows?`<ol class="operation-steps">${stepRows}</ol>`:''}
      ${warnings.length?`<div class="operation-warnings"><strong>降级与警告</strong>${warnings.map(item=>`<p>${escapeHtml(item)}</p>`).join('')}</div>`:''}
      ${d.suggestion?`<div class="operation-suggestion"><strong>建议处理</strong><p>${escapeHtml(d.suggestion)}</p></div>`:''}
      ${(d.operation_id||d.trace_id)?`<footer>${d.operation_id?`<code>operation ${escapeHtml(d.operation_id)}</code>`:''}${d.trace_id?`<code>trace ${escapeHtml(d.trace_id)}</code>`:''}${trace}</footer>`:''}
    </div>
  </details>`;
}

document.addEventListener("click", event => {
  const target = event.target instanceof Element ? event.target.closest("[data-operation-trace]") : null;
  if (!target) return;
  const traceId = String(target.getAttribute("data-operation-trace") || "");
  if (!traceId) return;
  state.selectedTraceId = traceId;
  navigateToView("trace_detail");
});

document.addEventListener("toggle", event => {
  const current = event.target;
  if (!(current instanceof HTMLDetailsElement) || !current.open || !current.matches(".operation-diagnostic[data-operation-group]")) return;
  const group = current.getAttribute("data-operation-group");
  document.querySelectorAll(".operation-diagnostic[data-operation-group]").forEach(item => {
    if (item !== current && item.getAttribute("data-operation-group") === group) item.open = false;
  });
}, true);

async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = { ...(opts.headers || {}) };
  // 非 safe method：自动从 cookie 读 CSRF token 并注入 header
  if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
    const csrf = readCookie("personification_webui_csrf");
    if (csrf) headers["X-Personification-CSRF"] = csrf;
  }
  // 仅对 GET 做 in-flight 去重；同一 path 在响应回来之前的重复请求复用同一 Promise
  const dedupKey = method === "GET" ? path : null;
  if (dedupKey && _apiInflight.has(dedupKey)) {
    return _apiInflight.get(dedupKey);
  }
  const promise = (async () => {
    const requestOpts = { credentials: "include", ...opts, headers };
    if (method === "GET" && !requestOpts.signal && _viewAbortController) requestOpts.signal = _viewAbortController.signal;
    const res = await fetch(API + path, requestOpts);
    if (res.status === 401) {
      state.logged = false;
      refreshEligibleAdmins().finally(() => render());
      throw new Error("未登录");
    }
    if (!res.ok) {
      let payload = {message:res.statusText || "请求失败"};
      try { payload = await res.json(); } catch {}
      throw new ApiError(normalizeApiDiagnostic(payload, res.status), res.status, path);
    }
    return res.status === 204 ? null : await res.json();
  })();
  if (dedupKey) {
    _apiInflight.set(dedupKey, promise);
    promise.finally(() => { _apiInflight.delete(dedupKey); });
  }
  return promise;
}

function alertFlash(kind, text) { state.alert = { kind, text }; render(); setTimeout(() => { state.alert = null; render(); }, 4000); }

function restoreConfigSearchFocus(caret) {
  setTimeout(() => {
    const el = document.getElementById("config-search-input");
    if (!el) return;
    el.focus();
    const pos = Number.isFinite(caret) ? Math.max(0, Math.min(caret, el.value.length)) : el.value.length;
    try { el.setSelectionRange(pos, pos); } catch {}
  }, 0);
}

function onConfigSearchCompositionStart(input) {
  state.configSearchComposing = true;
  state.configSearchDraft = input ? input.value : state.configSearch;
}

function onConfigSearchCompositionEnd(input) {
  state.configSearchComposing = false;
  state.configSearch = input ? input.value : state.configSearchDraft;
  state.configSearchDraft = "";
  const caret = input ? input.selectionStart : undefined;
  render();
  restoreConfigSearchFocus(caret);
}

function onConfigSearchInput(input, event) {
  if (!input) return;
  if ((event && event.isComposing) || state.configSearchComposing) {
    state.configSearchDraft = input.value;
    return;
  }
  state.configSearch = input.value;
  const caret = input.selectionStart;
  render();
  restoreConfigSearchFocus(caret);
}

async function bootstrap() {
  // 主题
  const savedTheme = localStorage.getItem("personification_theme") || "dark";
  state.theme = savedTheme;
  document.documentElement.setAttribute("data-theme", savedTheme);
  try { const me = await api("/auth/me"); state.logged = true; state.devicePending = false; state.qq = me.qq; await loadView(); }
  catch (e) { state.logged = false; state.devicePending = /DEVICE_PENDING/.test(String(e && e.message || "")); }
  if (state.devicePending) { render(); return; }
  if (!state.logged) await refreshEligibleAdmins();
  render();
}

async function refreshEligibleAdmins() {
  try {
    const response = await fetch(API + "/auth/eligible-admins", {credentials:"include"});
    const data = await response.json();
    state.eligibleAdmins = data.admins || [];
  } catch { state.eligibleAdmins = []; }
}

function toggleTheme() {
  state.theme = state.theme === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", state.theme);
  localStorage.setItem("personification_theme", state.theme);
  render();
}

function toggleMobileNav() {
  state.mobileNavOpen = !state.mobileNavOpen;
  render();
}

function closeMobileNav() {
  // 点击导航项后关闭抽屉；即便点的是当前视图（hashchange 不触发）也立即收起。
  if (state.mobileNavOpen) { state.mobileNavOpen = false; render(); }
}

document.addEventListener("keydown",event=>{if(event.key==="Escape"&&state.mobileNavOpen)closeMobileNav();});

function loadingMessageForView(view) {
  return ({
    dashboard: "正在统计 Token 消耗...",
    health: "正在跑功能体检...",
    qzone: "正在读取 QQ 空间状态...",
    config: "正在加载配置中心...",
    personas: "正在读取用户画像...",
    groups: "正在整理群信息...",
    memory: "正在打开记忆宫殿...",
    memory_graph: "正在绘制记忆关系...",
    stickers: "正在加载表情包库...",
    skills: "正在扫描 Skill 和 MCP 工具...",
    tool_creator: "正在恢复工具创建任务...",
    plugin_knowledge: "正在读取插件知识库...",
    plugin_manager: "正在检查插件更新...",
    persona_builder: "正在准备人设构建工具...",
    logs: "正在拉取插件日志...",
    traces: "正在拉取消息 Trace...",
    trace_detail: "正在打开 Trace 详情...",
    audit: "正在读取审计记录...",
    qq: "正在读取 QQ 账号信息...",
    agent_status: "正在汇总 Agent 运行状态...",
    data_transfer: "正在打开安全迁移工作台...",
  })[view] || "正在加载页面...";
}

async function loadView() {
  const navigationId = ++_navigationId;
  const view = state.view;
  _viewAbortController?.abort();
  _viewAbortController = new AbortController();
  await ensureViewAsset(view);
  if(navigationId!==_navigationId)return;
  state.loading = true;
  state.loadingMessage = loadingMessageForView(view);
  if (state.logged) render();
  try {
    if (view === "config") {
      const data = await api("/config/entries");
      state.entries = data.entries; state.groups = data.groups;
      if (!state.activeGroup || !state.groups.includes(state.activeGroup)) state.activeGroup = state.groups[0] || null;
    } else if (view === "devices") {
      const data = await api("/auth/devices");
      state.devices = data.devices; state.currentDeviceId = data.current_device_id;
    } else if (view === "dashboard") {
      state.dashboard = await api("/metrics/summary?window=" + encodeURIComponent(state.dashboardWindow));
    } else if (view === "personas") {
      const data = await api("/personas");
      state.personas = data.profiles; state.personasAvailable = data.available;
    } else if (view === "groups") {
      const data = await api("/groups");
      state.groupList = data.groups; state.groupsAvailable = data.available;
    } else if (view === "group_switch") {
      const data = await api("/groups/whitelist");
      state.groupSwitches = data.groups;
    } else if (view === "skills") {
      const [data, sourceData, installationData] = await Promise.all([
        api("/skills"),
        api("/mcp/sources").catch(() => ({sources:[]})),
        api("/mcp/installations").catch(() => ({installations:[]})),
      ]);
      state.skills = data.skills; state.skillsAvailable = data.available;
      state.skillSummary = data.summary || null;
      state.skillRemoteSources = data.remote_sources || [];
      state.skillMcpTools = data.mcp_tools || [];
      state.mcpSources = sourceData.sources || [];
      state.mcpInstallations = installationData.installations || [];
    } else if (view === "tool_creator") {
      const data = await api("/tool-creator/tasks?limit=40");
      state.toolCreatorTasks = data.tasks || [];
      if (!state.toolCreatorSelectedId && state.toolCreatorTasks.length) state.toolCreatorSelectedId = state.toolCreatorTasks[0].task_id;
      if (state.toolCreatorSelectedId) {
        state.toolCreatorDetail = await api("/tool-creator/tasks/" + encodeURIComponent(state.toolCreatorSelectedId)).catch(() => null);
      } else {
        state.toolCreatorDetail = null;
      }
      if (typeof startToolCreatorPolling === "function") startToolCreatorPolling();
    } else if (view === "test") {
      /* nothing to preload */
    } else if (view === "persona_builder") {
      const [history, botInfo] = await Promise.all([
        api("/persona-template/history?limit=8").catch(() => ({ records: [] })),
        api("/qq/info").catch(() => ({ bots: [] })),
      ]);
      state.personaTemplateHistory = history.records || [];
      state.qqInfo = botInfo;
      const botIds=(botInfo.bots||[]).map(item=>String(item.bot_id||"")).filter(Boolean);
      if(!botIds.includes(state.personaProfileBotId))state.personaProfileBotId=botIds[0]||"";
    } else if (view === "qq") {
      const [info, groups, friends] = await Promise.all([
        api("/qq/info").catch(e => ({ error: e.message })),
        api("/qq/groups").catch(() => ({ groups: [] })),
        api("/qq/friends").catch(() => ({ friends: [] })),
      ]);
      state.qqInfo = info; state.qqGroups = groups.groups || []; state.qqFriends = friends.friends || [];
    } else if (view === "health") {
      state.health = await api("/health/check");  // 默认读缓存，秒开
    } else if (view === "qzone") {
      state.qzone = await api("/qzone/status", {cache:"no-store"});
      if (typeof syncPersistedQzoneOperation === "function") await syncPersistedQzoneOperation(state.qzone);
      if (typeof startQzoneSnapshotScheduler === "function") startQzoneSnapshotScheduler(false);
    } else if (view === "plugin_manager") {
      const [status, history] = await Promise.all([
        api("/plugin-manager/status"),
        api("/plugin-manager/history?limit=30"),
      ]);
      state.pluginUpdateStatus = status;
      state.pluginUpdateHistory = history;
    } else if (view === "persona_prompt") {
      const qs = state.personaPromptPath ? ("?path=" + encodeURIComponent(state.personaPromptPath)) : "";
      state.personaPrompt = await api("/test/persona-prompt" + qs);
    } else if (view === "proactive") {
      const qs = new URLSearchParams({ since_hours: "72" });
      if (state.proactiveScope) qs.set("scope", state.proactiveScope);
      const [stats, recent] = await Promise.all([
        api("/proactive/stats?" + qs.toString()),
        api("/proactive/recent?limit=80" + (state.proactiveScope?`&scope=${encodeURIComponent(state.proactiveScope)}`:"")),
      ]);
      state.proactiveStats = stats;
      state.proactiveRecent = recent;
    } else if (view === "audit") {
      const qs = new URLSearchParams({ limit: "150" });
      if (state.auditFilter) qs.set("action", state.auditFilter);
      state.audit = await api("/audit/recent?" + qs.toString());
    } else if (view === "logs") {
      const qs = new URLSearchParams({ limit: "100" });
      if (state.logLevel) qs.set("level", state.logLevel);
      if (state.logQuery) qs.set("q", state.logQuery);
      if (state.logTraceId) qs.set("trace_id", state.logTraceId);
      const logs = await api("/logs/recent?" + qs.toString());
      state.logs = logs;
    } else if (view === "traces") {
      state.traces = await api("/logs/traces?limit=120");
    } else if (view === "trace_detail") {
      if (!state.selectedTraceId) {
        state.traceDetail = { error: "未选择 trace" };
      } else {
        state.traceDetail = await api("/logs/trace/" + encodeURIComponent(state.selectedTraceId)).catch(e => ({ error: e.message }));
      }
    } else if (view === "stickers") {
      state.stickers = await api("/stickers");
    } else if (view === "memory") {
      const qs = new URLSearchParams({ limit: String(state.memoryLimit || 200) });
      if (state.memoryFilter) qs.set("memory_type", state.memoryFilter);
      if (state.memoryIncludeSelf) qs.set("include_self", "true");
      if (state.memoryUserId) qs.set("user_id", state.memoryUserId);
      if (state.memoryGroupId) qs.set("group_id", state.memoryGroupId);
      if (state.memoryPalaceZone) qs.set("palace_zone", state.memoryPalaceZone);
      const [mem, inner, zones, vectorIndex] = await Promise.all([
        api("/memory/recent?" + qs.toString()),
        api("/memory/inner-state").catch(() => ({available: false})),
        api("/memory/palace-zones").catch(() => ({zones: []})),
        api("/memory/vector-index").catch(e => ({available:false, error:e.message})),
      ]);
      state.memory = mem;
      state.memoryInnerState = inner;
      state.memoryPalaceZones = zones.zones || [];
      state.memoryVectorIndex = vectorIndex;
    } else if (view === "plugin_knowledge") {
      const data = await api("/plugin-knowledge/list");
      state.pluginKnowledgeList = data.plugins || [];
      state.pluginKnowledgeAvailable = data.available;
      state.pluginKnowledgeTotal = data.total || 0;
      state.pluginKnowledgeDiagnostic = data.diagnostic || null;
    } else if (view === "memory_graph") {
      const qs = new URLSearchParams({ limit: String(state.memoryGraphLimit || 100) });
      if (state.memoryGraphGroupId) qs.set("group_id", state.memoryGraphGroupId);
      if (state.memoryGraphMinSalience) qs.set("min_salience", String(state.memoryGraphMinSalience));
      // 群下拉依赖 groupList；如果还没加载过，顺手加载一次
      const tasks = [api("/memory/graph?" + qs.toString()).catch(e => ({available:false, error:e.message}))];
      if (!state.groupList.length) tasks.push(api("/groups").catch(() => ({groups:[]})));
      const [graph, groupsResp] = await Promise.all(tasks);
      state.memoryGraph = graph;
      if (groupsResp && groupsResp.groups) {
        state.groupList = groupsResp.groups;
        state.groupsAvailable = groupsResp.available;
      }
    } else if (view === "agent_status") {
      state.agentStatus = await api("/agent-status");
    } else if (view === "data_transfer") {
      state.transferBotInfo = await api("/qq/info").catch(() => null);
    }
  } catch (e) {
    if (e && e.name === "AbortError") return;
    throw e;
  } finally {
    if (navigationId === _navigationId) { state.loading = false; state.loadingMessage = ""; }
  }
}

function viewTitle() {
  return ({agent_status:"Agent 状态",data_transfer:"数据迁移",dashboard:"仪表盘",config:"配置中心",personas:"用户画像",groups:"群信息",group_switch:"群开关",memory:"Agent 记忆",memory_graph:"记忆宫殿",stickers:"表情包",skills:"Skill 管理",tool_creator:"创建工具",plugin_knowledge:"插件知识库",plugin_manager:"插件管理",test:"模型测试",persona_prompt:"人设预览",persona_builder:"人设构建",audit:"审计日志",logs:"插件日志",traces:"消息 Trace",trace_detail:"Trace 详情",proactive:"主动诊断",health:"功能体检",qzone:"QQ 空间",qq:"QQ 管理",devices:"设备管理"})[state.view] || state.view;
}

async function navigateToView(view,{fromHistory=false}={}) {
  const nextView=normalizeView(view);
  captureScrollState();
  if(!fromHistory&&location.hash!==`#${nextView}`)history.pushState({view:nextView},"",`#${nextView}`);
  if(state.view==="qzone"&&nextView!=="qzone"&&typeof stopQzoneViewLifecycle==="function")stopQzoneViewLifecycle();
  if(state.view==="tool_creator"&&nextView!=="tool_creator"&&typeof stopToolCreatorPolling==="function")stopToolCreatorPolling();
  state.view=nextView;
  if(state.mobileNavOpen)state.mobileNavOpen=false;
  try{await loadView();render();}catch(e){alertFlash("err",e.message);}
}

function render() {
  const root = document.getElementById("app");
  captureScrollState();
  if (state.devicePending) { root.innerHTML = renderDevicePending(); return; }
  if (!state.logged) {
    if (state.view === "qzone" && typeof stopQzoneViewLifecycle === "function") stopQzoneViewLifecycle();
    root.innerHTML = renderLogin(); attachLogin(); return;
  }
  // 全量 innerHTML 重绘会让正在输入的搜索框失焦；记下焦点 + 光标位置，重绘后还原。
  const active = document.activeElement;
  let focusSnap = null;
  if (active && active.id && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) {
    focusSnap = {
      id: active.id,
      start: active.selectionStart,
      end: active.selectionEnd,
      scrollTop: active.scrollTop,
    };
  }
  root.innerHTML = renderLayout();
  attachLayout();
  flushOperationAnnouncement();
  restoreScrollState();
  if (focusSnap) {
    const next = document.getElementById(focusSnap.id);
    if (next && (next.tagName === "INPUT" || next.tagName === "TEXTAREA")) {
      next.focus();
      try {
        if (focusSnap.start !== null && focusSnap.end !== null) {
          next.setSelectionRange(focusSnap.start, focusSnap.end);
        }
        next.scrollTop = focusSnap.scrollTop || 0;
      } catch (_) { /* number/email inputs 不支持 setSelectionRange，忽略 */ }
    }
  }
}

const ICON_PATHS = {
  "activity": '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  "alert-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5m0 3h.01"/>',
  "alert-triangle": '<path d="M12 3 2.5 20h19L12 3Z"/><path d="M12 9v4m0 3h.01"/>',
  "archive": '<path d="M4 7h16v13H4V7Zm-1-3h18v3H3V4Zm6 7h6"/>',
  "book": '<path d="M4 5.5A3.5 3.5 0 0 1 7.5 2H11v18H7.5A3.5 3.5 0 0 0 4 23V5.5ZM20 5.5A3.5 3.5 0 0 0 16.5 2H13v18h3.5A3.5 3.5 0 0 1 20 23V5.5Z"/>',
  "bot": '<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M9 12h.01M15 12h.01M8 16h8M12 3v4"/>',
  "brain": '<path d="M9.5 4A3.5 3.5 0 0 0 6 7.5v.2A3.5 3.5 0 0 0 4 11v1a3.5 3.5 0 0 0 2 3.2v.3A3.5 3.5 0 0 0 9.5 19H12V4H9.5ZM14.5 4A3.5 3.5 0 0 1 18 7.5v.2a3.5 3.5 0 0 1 2 3.3v1a3.5 3.5 0 0 1-2 3.2v.3a3.5 3.5 0 0 1-3.5 3.5H12V4h2.5Z"/>',
  "check": '<path d="m5 12 4 4L19 6"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/>',
  "gauge": '<path d="M4 17a8 8 0 1 1 16 0M12 13l4-4"/>',
  "heart-pulse": '<path d="M3 12h4l2-4 4 8 2-4h6M20 7c-1.8-3-6-2.5-8 1-2-3.5-6.2-4-8-1"/>',
  "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 15-5-5L5 20"/>',
  "layers": '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
  "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
  "message-square": '<path d="M4 4h16v13H8l-4 4V4Z"/>',
  "moon": '<path d="M20 15.5A8 8 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z"/>',
  "plug": '<path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-6 6v4m-3 0h6"/>',
  "refresh": '<path d="M20 7v5h-5M4 17v-5h5M6.1 8a7 7 0 0 1 11.4-2L20 9M4 15l2.5 3a7 7 0 0 0 11.4-2"/>',
  "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  "settings": '<circle cx="12" cy="12" r="3"/><path d="M19 13.5v-3l-2-.7-.8-1.9.9-1.9-2.1-2.1-1.9.9-1.9-.8L10.5 2h-3l-.7 2-1.9.8-1.9-.9L.9 6l.9 1.9-.8 1.9-2 .7v3l2 .7.8 1.9-.9 1.9L3 20.1l1.9-.9 1.9.8.7 2h3l.7-2 1.9-.8 1.9.9 2.1-2.1-.9-1.9.8-1.9 2-.7Z" transform="translate(2) scale(.84)"/>',
  "shield": '<path d="M12 3 20 7v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4Zm-3 9 2 2 4-5"/>',
  "sparkles": '<path d="m12 3 1.3 3.7L17 8l-3.7 1.3L12 13l-1.3-3.7L7 8l3.7-1.3L12 3ZM5 14l.8 2.2L8 17l-2.2.8L5 20l-.8-2.2L2 17l2.2-.8L5 14Zm13-1 1 2.8 3 1.2-3 1.2L18 21l-1-2.8-3-1.2 3-1.2 1-2.8Z"/>',
  "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3m5 0h5"/>',
  "transfer": '<path d="M7 7h11m-3-3 3 3-3 3M17 17H6m3 3-3-3 3-3"/>',
  "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8m13 18v-2a4 4 0 0 0-3-3.87"/>',
  "user-cog": '<circle cx="9" cy="8" r="4"/><path d="M2 21v-2a5 5 0 0 1 5-5h4m7-1v2m0 4v2m-4-4h2m4 0h2"/>',
  "wand": '<path d="m15 4 5 5L8 21H3v-5L15 4Zm-4-1 1 2M21 13l-2-1M18 2l-1 2"/>',
  "wrench": '<path d="M14 6a4 4 0 0 0-5 5L3 17l4 4 6-6a4 4 0 0 0 5-5l-3 3-3-3 2-4Z"/>',
};

function renderIcon(name, className="ui-icon") {
  const content = ICON_PATHS[name] || ICON_PATHS.gauge;
  return `<svg class="${escapeAttr(className)}" viewBox="0 0 24 24" aria-hidden="true">${content}</svg>`;
}

function renderLayout() {
  const navItem = (v,label,icon="gauge") => `<a href="#${v}" class="${state.view===v?'active':''}" aria-current="${state.view===v?'page':'false'}">${renderIcon(icon,'nav-icon')}<span>${label}</span></a>`;
  const themeIcon = state.theme === "dark" ? renderIcon("sun") : renderIcon("moon");
  const themeLabel = state.theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
  const loadingHint = state.loading
    ? `<div class="loading-hint"><span class="spinner"></span><span>${escapeHtml(state.loadingMessage || "正在加载页面...")}</span></div>`
    : "";
  return `${state.loading ? '<div class="progress-bar"></div>' : ''}
    <div id="operation-live-region" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>
    <div class="layout">
    ${state.mobileNavOpen ? '<div class="scrim" onclick="toggleMobileNav()"></div>' : ''}
    <aside id="console-sidebar" class="${state.mobileNavOpen?'open':''}">
      <h1>拟人插件控制台</h1>
      <nav aria-label="控制台导航">
        <div class="nav-group-label">运行</div>
        ${navItem('agent_status','Agent 状态','activity')}
        ${navItem('dashboard','仪表盘','gauge')}
        ${navItem('health','功能体检','heart-pulse')}
        ${navItem('qzone','QQ 空间','sparkles')}
        ${navItem('config','配置中心','settings')}
        <div class="nav-group-label">拟人与记忆</div>
        ${navItem('personas','用户画像','users')}
        ${navItem('groups','群信息','message-square')}
        ${navItem('group_switch','群开关','layers')}
        ${navItem('memory','Agent 记忆','brain')}
        ${navItem('memory_graph','记忆宫殿','database')}
        ${navItem('stickers','表情包','image')}
        <div class="nav-group-label">能力</div>
        ${navItem('skills','Skill 管理','plug')}
        ${navItem('tool_creator','创建工具','wand')}
        ${navItem('plugin_knowledge','插件知识库','book')}
        ${navItem('plugin_manager','插件管理','wrench')}
        ${navItem('test','模型测试','terminal')}
        ${navItem('persona_prompt','人设预览','bot')}
        ${navItem('persona_builder','人设构建','wand')}
        ${navItem('proactive','主动诊断','activity')}
        ${navItem('traces','消息 Trace','search')}
        <div class="nav-group-label">运维</div>
        ${navItem('data_transfer','数据迁移','transfer')}
        ${navItem('audit','审计日志','shield')}
        ${navItem('logs','插件日志','archive')}
        ${navItem('qq','QQ 管理','user-cog')}
        ${navItem('devices','设备管理','shield')}
      </nav>
    </aside>
    <main data-view="${escapeAttr(state.view)}" data-loading="${state.loading?'true':'false'}">
      <div class="topbar between">
        <div style="display:flex;align-items:center;min-width:0;flex:1">
           <button class="mobile-nav-toggle" onclick="toggleMobileNav()" aria-label="菜单" aria-controls="console-sidebar" aria-expanded="${state.mobileNavOpen?'true':'false'}">${renderIcon('menu')}</button>
          <div style="min-width:0">
            <div class="breadcrumb">控制台 <span class="sep">›</span> ${escapeHtml(viewTitle())}</div>
            <strong style="font-size:17px">${escapeHtml(viewTitle())}</strong>
          </div>
        </div>
        <div class="row">
          <button class="btn small icon-btn" onclick="toggleTheme()" title="${themeLabel}" aria-label="${themeLabel}">${themeIcon}</button>
          <span class="muted" title="登录 QQ">${escapeHtml(state.qq)}</span>
          <button class="btn small" onclick="doLogout()">退出</button>
        </div>
      </div>
      ${state.alert ? `<div class="alert ${state.alert.kind}">${escapeHtml(state.alert.text)}</div>` : ''}
      ${loadingHint}
      ${renderView()}
    </main>
  </div>`;
}

function renderView() {
  if (state.view === "config") return renderConfig();
  if (state.view === "devices") return renderDevices();
  if (state.view === "dashboard") return renderDashboard();
  if (state.view === "personas") return renderPersonas();
  if (state.view === "groups") return renderGroups();
  if (state.view === "group_switch") return renderGroupSwitch();
  if (state.view === "skills") return renderSkills();
  if (state.view === "tool_creator") return renderToolCreator();
  if (state.view === "plugin_knowledge") return renderPluginKnowledge();
  if (state.view === "plugin_manager") return renderPluginManager();
  if (state.view === "qq") return renderQQ();
  if (state.view === "health") return renderHealth();
  if (state.view === "qzone") return renderQzone();
  if (state.view === "test") return renderTest();
  if (state.view === "persona_prompt") return renderPersonaPrompt();
  if (state.view === "persona_builder") return renderPersonaBuilder();
  if (state.view === "memory") return renderMemory();
  if (state.view === "memory_graph") return renderMemoryGraph();
  if (state.view === "stickers") return renderStickers();
  if (state.view === "audit") return renderAudit();
  if (state.view === "logs") return renderLogs();
  if (state.view === "traces") return renderTraces();
  if (state.view === "trace_detail") return renderTraceDetail();
  if (state.view === "proactive") return renderProactive();
  if (state.view === "agent_status") return renderAgentStatus();
  if (state.view === "data_transfer") return renderDataTransfer();
  return `<div class="card"><h2>${escapeHtml(viewTitle())}</h2><p class="muted">该视图暂未实现。</p></div>`;
}
