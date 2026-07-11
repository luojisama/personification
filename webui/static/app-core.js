const API = "/personification/api";
let state = {
  logged: false, qq: "", view: "dashboard",
  entries: [], groups: [], activeGroup: null, configSearch: "", configSearchComposing: false, configSearchDraft: "",
  devices: [], pendingDevices: [], trustedDevices: [], devicePending: false, loginRequestId: "", loginPolling: false, alert: null, loading: false, loadingMessage: "",
  dashboard: null, dashboardWindow: "month", dashboardDetail: null,
  personas: [], selectedPersona: null, personaSearch: "",
  groupList: [], selectedGroup: null, groupPersonas: [], groupStyle: null, groupKnowledge: [], groupAliasDrafts: {}, groupSchedule: null, groupScheduleGenerating: false,
  groupFavorability: null,
  groupSwitches: [], newGroupId: "",
  skills: [], skillFilter: "", skillSummary: null, skillRemoteSources: [], skillMcpTools: [],
  skillSourceForm: { source: "", name: "", ref: "", subdir: "", kind: "auto", preferFirst: false, autoApprove: false },
  testPrompt: "你好，自我介绍一下", testSystem: "你是测试助手，简洁回复。", testResult: null, testAllResult: null,
  personaTemplateForm: { mode: "source", work_title: "", character_name: "", persona_name: "", gender: "", personality: "", traits: "", hobbies: "", description: "" }, personaTemplateResult: null, personaTemplateBusy: false, personaTemplateTask: null, personaTemplateHistory: [],
  personaAvatarCandidateId: "", personaSignatureCandidateId: "", personaProfileBotId: "",
  personaPrompt: null, personaPromptPath: "", health: null, healthBusyCat: "", interactionResult: null, interactionBusy: false,
  qzoneForwardForm: { target_user_id: "", forward_text: "" }, qzoneForwardResult: null, qzoneForwardBusy: false,
  qzoneActionBusy: "", qzoneActionResult: null, qzoneOperationId: "",
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
  skills:"app-tools.js",plugin_knowledge:"app-tools.js",plugin_manager:"app-tools.js",test:"app-tools.js",
  proactive:"app-activity.js",audit:"app-activity.js",logs:"app-activity.js",traces:"app-activity.js",trace_detail:"app-activity.js",
  agent_status:"app-operations.js",data_transfer:"app-operations.js",
};
const _loadedAssets = new Set(["app-core.js","app-auth.js"]);
const _assetInflight = new Map();
let _viewAbortController = null;
let _navigationId = 0;

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
    if (res.status === 401) { state.logged = false; render(); throw new Error("未登录"); }
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch {}
      throw new Error(detail);
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
  if (!state.logged) {
    try { const ea = await fetch(API + "/auth/eligible-admins").then(r=>r.json()); state.eligibleAdmins = ea.admins||[]; } catch {}
  }
  render();
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
      const [data, pend, trust] = await Promise.all([
        api("/auth/devices"),
        api("/auth/pending-devices").catch(() => ({ devices: [] })),
        api("/auth/trusted-devices").catch(() => ({ devices: [] })),
      ]);
      state.devices = data.devices; state.currentDeviceId = data.current_device_id;
      state.pendingDevices = pend.devices || [];
      state.trustedDevices = trust.devices || [];
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
      const data = await api("/skills");
      state.skills = data.skills; state.skillsAvailable = data.available;
      state.skillSummary = data.summary || null;
      state.skillRemoteSources = data.remote_sources || [];
      state.skillMcpTools = data.mcp_tools || [];
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
      state.qzone = await api("/qzone/status");
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
  return ({agent_status:"Agent 状态",data_transfer:"数据迁移",dashboard:"仪表盘",config:"配置中心",personas:"用户画像",groups:"群信息",group_switch:"群开关",memory:"Agent 记忆",memory_graph:"记忆宫殿",stickers:"表情包",skills:"Skill 管理",plugin_knowledge:"插件知识库",plugin_manager:"插件管理",test:"模型测试",persona_prompt:"人设预览",persona_builder:"人设构建",audit:"审计日志",logs:"插件日志",traces:"消息 Trace",trace_detail:"Trace 详情",proactive:"主动诊断",health:"功能体检",qzone:"QQ 空间",qq:"QQ 管理",devices:"设备管理"})[state.view] || state.view;
}

async function navigateToView(view,{fromHistory=false}={}) {
  if(!fromHistory&&location.hash!==`#${view}`)history.pushState(null,"",`#${view}`);
  state.view=view;
  if(state.mobileNavOpen)state.mobileNavOpen=false;
  try{await loadView();render();}catch(e){alertFlash("err",e.message);}
}

function render() {
  const root = document.getElementById("app");
  if (state.devicePending) { root.innerHTML = renderDevicePending(); return; }
  if (!state.logged) { root.innerHTML = renderLogin(); attachLogin(); return; }
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

function renderLayout() {
  const navIcon = (kind) => `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${kind==='pulse'?'M3 12h4l2-6 4 12 2-6h6':kind==='transfer'?'M7 7h11m-3-3 3 3-3 3M17 17H6m3 3-3-3 3-3':kind==='shield'?'M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4m-3 9l2 2 4-5':kind==='users'?'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8m13 18v-2a4 4 0 0 0-3-3.87':'M4 17a8 8 0 1 1 16 0M12 13l4-4'}"/></svg>`;
  const navItem = (v,label,icon="gauge") => `<a href="#${v}" class="${state.view===v?'active':''}" aria-current="${state.view===v?'page':'false'}">${navIcon(icon)}<span>${label}</span></a>`;
  const themeIcon = state.theme === "dark" ? "🌙" : "☀";
  const loadingHint = state.loading
    ? `<div class="loading-hint"><span class="spinner"></span><span>${escapeHtml(state.loadingMessage || "正在加载页面...")}</span></div>`
    : "";
  return `${state.loading ? '<div class="progress-bar"></div>' : ''}
    <div class="layout">
    ${state.mobileNavOpen ? '<div class="scrim" onclick="toggleMobileNav()"></div>' : ''}
    <aside id="console-sidebar" class="${state.mobileNavOpen?'open':''}">
      <h1>拟人插件控制台</h1>
      <nav aria-label="控制台导航">
        <div class="nav-group-label">运行</div>
        ${navItem('agent_status','Agent 状态','pulse')}
        ${navItem('dashboard','仪表盘')}
        ${navItem('health','功能体检')}
        ${navItem('qzone','QQ 空间')}
        ${navItem('config','配置中心')}
        <div class="nav-group-label">拟人与记忆</div>
        ${navItem('personas','用户画像','users')}
        ${navItem('groups','群信息')}
        ${navItem('group_switch','群开关')}
        ${navItem('memory','Agent 记忆')}
        ${navItem('memory_graph','记忆宫殿')}
        ${navItem('stickers','表情包')}
        <div class="nav-group-label">能力</div>
        ${navItem('skills','Skill 管理')}
        ${navItem('plugin_knowledge','插件知识库')}
        ${navItem('plugin_manager','插件管理')}
        ${navItem('test','模型测试')}
        ${navItem('persona_prompt','人设预览')}
        ${navItem('persona_builder','人设构建')}
        ${navItem('proactive','主动诊断')}
        ${navItem('traces','消息 Trace')}
        <div class="nav-group-label">运维</div>
        ${navItem('data_transfer','数据迁移','transfer')}
        ${navItem('audit','审计日志','shield')}
        ${navItem('logs','插件日志')}
        ${navItem('qq','QQ 管理')}
        ${navItem('devices','设备管理')}
      </nav>
    </aside>
    <main>
      <div class="topbar between">
        <div style="display:flex;align-items:center;min-width:0;flex:1">
           <button class="mobile-nav-toggle" onclick="toggleMobileNav()" aria-label="菜单" aria-controls="console-sidebar" aria-expanded="${state.mobileNavOpen?'true':'false'}">≡</button>
          <div style="min-width:0">
            <div class="breadcrumb">控制台 <span class="sep">›</span> ${escapeHtml(viewTitle())}</div>
            <strong style="font-size:17px">${escapeHtml(viewTitle())}</strong>
          </div>
        </div>
        <div class="row">
          <button class="btn small" onclick="toggleTheme()" title="切换主题">${themeIcon}</button>
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
