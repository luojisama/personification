const _MCP_OPERATION_RESULT_STORAGE_KEY = "personification_mcp_operation_result_v1";
let _mcpPendingInstall = null;

function clearMcpSensitiveState() {
  _mcpPendingInstall = null;
}

function persistMcpOperationResult(input) {
  const result = input && input.diagnostic && typeof input.diagnostic === "object" ? input.diagnostic : input;
  state.mcpOperationResult = result && typeof result === "object" ? result : null;
  try {
    if (state.mcpOperationResult) sessionStorage.setItem(_MCP_OPERATION_RESULT_STORAGE_KEY, JSON.stringify(state.mcpOperationResult));
    else sessionStorage.removeItem(_MCP_OPERATION_RESULT_STORAGE_KEY);
  } catch {}
}

try {
  const savedMcpOperationResult = JSON.parse(sessionStorage.getItem(_MCP_OPERATION_RESULT_STORAGE_KEY) || "null");
  if (savedMcpOperationResult && typeof savedMcpOperationResult === "object") state.mcpOperationResult = savedMcpOperationResult;
} catch {
  try { sessionStorage.removeItem(_MCP_OPERATION_RESULT_STORAGE_KEY); } catch {}
}

function stopMcpViewLifecycle() {
  // Pending confirmation may contain Secret input values and must stay page-local.
  _mcpPendingInstall = null;
}

function mcpSourceById(sourceId) {
  return (state.mcpSources || []).find(source => source.id === sourceId) || {
    id: sourceId || "unknown",
    name: sourceId || "Unknown Registry",
    preview: false,
  };
}

function renderMcpSourceBadges(sourceId) {
  const source = mcpSourceById(sourceId);
  const authority = source.id === "official"
    ? '<span class="mcp-source-badge official">Official</span>'
    : '<span class="mcp-source-badge compatible">compatible</span>';
  const preview = source.preview ? '<span class="mcp-source-badge preview">Preview</span>' : "";
  return `${authority}${preview}`;
}

function renderMcpExternalLink(url, label) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  const safe = safeHttpUrl(raw);
  return safe
    ? `<a href="${escapeAttr(safe)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
    : "";
}

function renderMcpOperationResult() {
  const result = state.mcpOperationResult;
  if (!result) return "";
  return `<section class="card mcp-diagnostic-card">
    <div class="mcp-section-heading">
      <div><span class="eyebrow">MCP OPERATION</span><h2>最近一次 MCP 操作</h2><p>刷新后仍保留；只存储服务端返回或基于明确成功响应生成的脱敏 diagnostic。</p></div>
      <button class="btn small" data-mcp-operation-clear>清除</button>
    </div>
    ${renderOperationDiagnostic(result, {group:"mcp-operation"})}
  </section>`;
}

function mcpStatusTone(status) {
  const value = String(status || "unknown").toLowerCase();
  if (["active", "running", "ready"].includes(value)) return "ok";
  if (["deprecated", "stopped", "unknown"].includes(value)) return "warn";
  return "error";
}

function renderMcpRegistryServer(server) {
  const repository = server.repository && typeof server.repository === "object" ? server.repository : {};
  const sourceId = server.source_id || state.mcpSourceId;
  const links = [
    renderMcpExternalLink(repository.url, "Repository"),
    renderMcpExternalLink(server.website || server.website_url, "Website"),
    renderMcpExternalLink(server.schema, "Schema"),
  ].filter(Boolean).join("");
  const resources = [
    ["repository", repository.url || repository.source || "未声明"],
    ["website", server.website || server.website_url || "未声明"],
    ["schema", server.schema || "未声明"],
  ].map(([label, value]) => `<span><b>${label}</b><code>${escapeHtml(value)}</code></span>`).join("");
  const status = String(server.status || "unknown");
  return `<article class="mcp-registry-record">
    <header>
      <div class="mcp-record-source">${renderMcpSourceBadges(sourceId)}</div>
      <span class="mcp-state ${mcpStatusTone(status)}"><i></i>${escapeHtml(status)}</span>
    </header>
    <div class="mcp-record-body">
      <div class="mcp-record-title"><h3>${escapeHtml(server.title || server.name || "Untitled Server")}</h3><code>${escapeHtml(server.name || "")}</code></div>
      <p>${escapeHtml(server.description || "未提供 description")}</p>
      ${server.status_message ? `<div class="mcp-status-message">${escapeHtml(server.status_message)}</div>` : ""}
      <div class="mcp-resource-facts">${resources}</div>
    </div>
    <footer>
      <div class="mcp-transport-counts"><span><strong>${Number(server.stdio_packages || 0)}</strong> stdio</span><span><strong>${Number(server.remote_count || 0)}</strong> remote</span><span><strong>${escapeHtml(server.version || "-")}</strong> version</span></div>
      <div class="mcp-record-links">${links}</div>
      <button class="btn small" data-mcp-detail="${escapeAttr(server.name || "")}" data-mcp-source="${escapeAttr(sourceId)}">查看详情</button>
    </footer>
  </article>`;
}

function renderMcpRegistryList() {
  const records = (state.mcpResults || []).map(renderMcpRegistryServer).join("");
  let empty = "";
  if (!records) {
    empty = state.mcpSearchLoaded
      ? '<div class="mcp-empty"><strong>没有匹配的 Server 名称</strong><span>Registry search 仅按 Server 名称查询，不执行能力全文搜索。</span></div>'
      : '<div class="mcp-empty"><strong>按 Server 名称开始 discovery</strong><span>输入 canonical name 或名称片段；这里不声称支持能力全文搜索。</span></div>';
  }
  const more = state.mcpNextCursor
    ? `<div class="mcp-load-more"><button class="btn" data-mcp-load-more ${state.mcpBusy ? "disabled" : ""}>${state.mcpLoadingMore ? '<span class="spinner"></span> 加载中' : '加载更多'}</button><span>opaque next cursor 将原样发送，结果追加并按 canonical name 去重。</span></div>`
    : (records && state.mcpSearchLoaded ? '<div class="mcp-stream-end">Registry result set complete</div>' : "");
  return `<div class="mcp-registry-stream">${records || empty}</div>${more}`;
}

function mcpPackageIdentity(item) {
  const type = String(item.registry_type || "").toLowerCase();
  const separator = type === "pypi" ? "==" : "@";
  return `${item.identifier || "unknown"}${separator}${item.version || "unknown"}`;
}

function renderMcpPackageMatrix(packages) {
  return `<div class="mcp-package-matrix">${packages.map(item => {
    const supported = item.supported === true;
    return `<article class="mcp-package-row ${supported ? "supported" : "unsupported"}">
      <div><span>${escapeHtml(item.registry_type || "unknown")} / ${escapeHtml(item.transport || "unknown")}</span><code>${escapeHtml(mcpPackageIdentity(item))}</code></div>
      <span class="mcp-state ${supported ? "ok" : "error"}"><i></i>${supported ? "supported" : "unsupported"}</span>
      ${item.fileSha256 ? `<small>fileSha256 ${escapeHtml(item.fileSha256)}</small>` : ""}
      ${!supported ? `<p>${escapeHtml(item.unsupported_reason || "该 package 不支持快捷安装。")}</p>` : ""}
    </article>`;
  }).join("") || '<div class="mcp-empty"><strong>没有 package metadata</strong><span>该 Server 当前没有可展示的安装包。</span></div>'}</div>`;
}

function selectedMcpPackage(packages) {
  const supported = packages.filter(item => item.supported === true);
  let selected = supported.find(item => Number(item.index) === Number(state.mcpPackageIndex));
  if (!selected && supported.length) {
    selected = supported[0];
    state.mcpPackageIndex = selected.index;
  }
  return selected || null;
}

function renderMcpDetail() {
  const detail = state.mcpDetail || {};
  const server = detail.server || {};
  const packages = Array.isArray(detail.packages) ? detail.packages : [];
  const selected = selectedMcpPackage(packages);
  const source = detail.source || mcpSourceById(state.mcpSourceId);
  const repository = server.repository && typeof server.repository === "object" ? server.repository : {};
  const externalLinks = [
    renderMcpExternalLink(repository.url, "Repository"),
    renderMcpExternalLink(server.website || server.website_url, "Website"),
    renderMcpExternalLink(server.schema, "Schema"),
  ].filter(Boolean).join("");
  const packageOptions = packages.filter(item => item.supported === true).map(item => `<option value="${Number(item.index)}" ${Number(item.index) === Number(state.mcpPackageIndex) ? "selected" : ""}>${escapeHtml(item.registry_type)} · ${escapeHtml(mcpPackageIdentity(item))}</option>`).join("");
  const inputs = selected ? (selected.inputs || []).map(input => {
    const choices = Array.isArray(input.choices) ? input.choices : [];
    const registryDefault = String(input.default == null ? "" : input.default);
    const defaultIsChoice = choices.some(choice => String(choice) === registryDefault);
    const control = choices.length
      ? `<select data-mcp-install-input="${escapeAttr(input.key)}" data-mcp-secret="${input.secret ? "true" : "false"}">${registryDefault && !defaultIsChoice ? `<option value="" selected>使用 Registry default · ${escapeHtml(registryDefault)}</option>` : ""}${choices.map(choice => `<option value="${escapeAttr(choice)}" ${String(choice) === registryDefault ? "selected" : ""}>${escapeHtml(choice)}</option>`).join("")}</select>`
      : `<input data-mcp-install-input="${escapeAttr(input.key)}" data-mcp-secret="${input.secret ? "true" : "false"}" type="${input.secret ? "password" : "text"}" placeholder="${escapeAttr(input.default || "")}">`;
    return `<label class="mcp-input"><span>${escapeHtml(input.key)}${input.required ? " *" : ""}<em>${escapeHtml(input.location || "input")}${input.secret ? " / Secret" : ""}</em></span><small>${escapeHtml(input.description || "Registry 未提供说明")}</small>${control}</label>`;
  }).join("") : "";
  const status = String(server.status || "unknown");
  return `<div class="mcp-detail">
    <div class="mcp-detail-nav"><button class="btn small" data-mcp-detail-close>返回 Registry 结果</button><div>${renderMcpSourceBadges(source.id || state.mcpSourceId)}</div></div>
    <header class="mcp-detail-head"><div><span class="eyebrow">CANONICAL SERVER</span><h3>${escapeHtml(server.title || server.name || "")}</h3><code>${escapeHtml(server.name || "")}</code></div><span class="mcp-state ${mcpStatusTone(status)}"><i></i>${escapeHtml(status)}</span></header>
    <p class="mcp-detail-description">${escapeHtml(server.description || "未提供 description")}</p>
    ${server.status_message ? `<div class="mcp-status-message">${escapeHtml(server.status_message)}</div>` : ""}
    <div class="mcp-detail-meta"><span>version<strong>${escapeHtml(server.version || "-")}</strong></span><span>schema<strong>${escapeHtml(server.schema || "未声明")}</strong></span></div>
    ${externalLinks ? `<div class="mcp-record-links">${externalLinks}</div>` : ""}
    <div><span class="eyebrow">PACKAGE SUPPORT</span>${renderMcpPackageMatrix(packages)}</div>
    ${selected ? `<div class="mcp-install-preflight">
      <div class="mcp-section-heading"><div><span class="eyebrow">EXECUTION PREFLIGHT</span><h3>安装预检</h3><p>执行第三方 package 前先核对精确 identity。安装后所有 tool 默认未授权。</p></div><span class="tag required">publisher metadata untrusted</span></div>
      <label class="mcp-install-target">安装目标<select id="mcp-package-select">${packageOptions}</select></label>
      <div class="mcp-input-grid">${inputs || '<p class="muted">该 package 不需要额外输入。</p>'}</div>
      <label class="mcp-install-target">工具名前缀（可选）<input id="mcp-prefix-input" placeholder="留空由服务端安全生成" value="${escapeAttr(state.mcpPrefix || "")}"></label>
      <div class="alert info">Registry 只提供 publisher metadata，不代表安全审计。Secret 只提交至服务器受限文件；确认页和 diagnostic 均不显示值。</div>
      <button class="btn primary" data-mcp-install-plan ${state.mcpBusy ? "disabled" : ""}>审阅 command plan</button>
    </div>` : '<div class="alert info">没有 supported 的 npm/PyPI stdio package；unsupported_reason 已逐项列出。</div>'}
  </div>`;
}

function renderMcpRegistryDiscovery() {
  const sources = state.mcpSources || [];
  const options = sources.map(source => `<option value="${escapeAttr(source.id)}" ${state.mcpSourceId === source.id ? "selected" : ""}>${escapeHtml(source.name)}</option>`).join("");
  return `<section class="card mcp-zone mcp-registry-zone">
    <div class="mcp-section-heading">
      <div><span class="eyebrow">01 / REGISTRY DISCOVERY</span><h2>Registry discovery</h2><p>Official Registry 优先，也可选择 configured compatible HTTPS source。搜索语义严格限定为 Server 名称。</p></div>
      <div class="mcp-source-legend"><span>来源</span>${renderMcpSourceBadges(state.mcpSourceId)}</div>
    </div>
    <div class="mcp-search-row"><select id="mcp-source-select" aria-label="Registry source" ${state.mcpBusy ? "disabled" : ""}>${options}</select><input id="mcp-search-input" type="search" placeholder="按 Server 名称搜索" value="${escapeAttr(state.mcpQuery || "")}" aria-label="按 Server 名称搜索" ${state.mcpBusy ? "disabled" : ""}><button class="btn primary" data-mcp-search ${state.mcpBusy ? "disabled" : ""}>${state.mcpBusy && !state.mcpLoadingMore ? '<span class="spinner"></span>' : ''} 搜索</button></div>
    <p class="mcp-search-scope">SEARCH SCOPE / canonical Server name only · 不声称支持能力全文搜索</p>
    ${state.mcpDetail ? renderMcpDetail() : renderMcpRegistryList()}
  </section>`;
}

function mcpSchemaType(spec) {
  if (!spec || typeof spec !== "object") return "unknown";
  const raw = Array.isArray(spec.type) ? spec.type.join(" | ") : String(spec.type || "unknown");
  const enumValues = Array.isArray(spec.enum) ? ` · enum(${spec.enum.length})` : "";
  return raw + enumValues;
}

function renderMcpInputSchema(schema) {
  const value = schema && typeof schema === "object" ? schema : {};
  const properties = value.properties && typeof value.properties === "object" ? value.properties : {};
  const required = new Set(Array.isArray(value.required) ? value.required.map(String) : []);
  const rows = Object.entries(properties).map(([name, spec]) => `<li><code>${escapeHtml(name)}</code><span>${escapeHtml(mcpSchemaType(spec))}${required.has(name) ? ' <b>required</b>' : ""}</span><small>${escapeHtml(spec && spec.description || "未提供参数说明")}</small></li>`).join("");
  return `<div class="mcp-schema-summary"><div class="mcp-schema-head"><span>Input schema</span><strong>${Object.keys(properties).length} parameters / ${required.size} required</strong></div><ul>${rows || '<li class="empty">未声明 input parameters</li>'}</ul></div>`;
}

function renderMcpSchemaDetails(label, schema, identity) {
  const value = schema && typeof schema === "object" ? schema : {};
  const properties = value.properties && typeof value.properties === "object" ? Object.keys(value.properties) : [];
  const summary = Object.keys(value).length
    ? `${String(value.type || "schema")} · ${properties.length} properties`
    : "未声明";
  return `<details class="mcp-json-details" data-detail-key="${escapeAttr(stableDetailKey("mcp-schema", identity, label))}"><summary><span>${escapeHtml(label)}</span><strong>${escapeHtml(summary)}</strong></summary><pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`;
}

function renderManagedMcpTool(tool, installation) {
  const authorized = tool.authorized === true;
  const registered = tool.registered === true;
  const effective = tool.effective === true;
  const processRunning = installation.process_state === "running" && installation.desired_enabled === true;
  const availability = effective
    ? "当前可调用"
    : authorized && !processRunning
      ? "已授权；Server 未运行，当前不可调用"
      : authorized
        ? "已授权；尚未有效注册，当前不可调用"
        : "未授权，当前不可调用";
  const annotations = tool.annotations && typeof tool.annotations === "object" ? tool.annotations : {};
  return `<article class="mcp-tool-card ${effective ? "effective" : ""}">
    <header><div><span class="eyebrow">${escapeHtml(tool.title || "MCP TOOL")}</span><h4>${escapeHtml(tool.remote_name || "")}</h4><code>${escapeHtml(tool.registered_name || "")}</code></div><button class="btn small ${authorized ? "danger" : "primary"}" data-mcp-tool-toggle="${escapeAttr(tool.remote_name || "")}" data-mcp-installation="${escapeAttr(installation.installation_id || "")}" data-mcp-enabled="${authorized ? "false" : "true"}" data-mcp-risk="${tool.publisher_read_only ? "read" : "unknown"}" ${state.mcpBusy ? "disabled" : ""}>${authorized ? "撤销授权" : "授权"}</button></header>
    <p>${escapeHtml(tool.description || "未提供 description")}</p>
    <div class="mcp-state-strip"><span class="${authorized ? "on" : "off"}">authorized / ${authorized ? "yes" : "no"}</span><span class="${registered ? "on" : "off"}">registered / ${registered ? "yes" : "no"}</span><span class="${effective ? "on" : "off"}">effective / ${effective ? "yes" : "no"}</span></div>
    <div class="mcp-availability ${effective ? "ok" : "muted"}">${escapeHtml(availability)}</div>
    ${renderMcpInputSchema(tool.inputSchema || tool.parameters)}
    <div class="mcp-schema-pair">${renderMcpSchemaDetails("Output schema", tool.outputSchema || tool.output_schema, tool.registered_name)}${renderMcpSchemaDetails("Annotations · publisher 声明，untrusted", annotations, tool.registered_name)}</div>
    <div class="mcp-publisher-warning">${tool.publisher_read_only ? "publisher 声明 readOnlyHint；该声明未受信任，也不会自动授权。" : "publisher 未声明可信只读语义；副作用按 unknown 处理。"}</div>
  </article>`;
}

function renderMcpInstallation(item) {
  const tools = Array.isArray(item.tools) ? item.tools : [];
  const metadata = item.metadata && typeof item.metadata === "object" ? item.metadata : {};
  const processState = String(item.process_state || item.observed_status || "stopped");
  const desired = item.desired_enabled === true;
  const sourceBadges = renderMcpSourceBadges(item.source_id || "unknown");
  const repository = metadata.repository && typeof metadata.repository === "object" ? metadata.repository : {};
  const links = [renderMcpExternalLink(repository.url, "Repository"), renderMcpExternalLink(metadata.website, "Website")].filter(Boolean).join("");
  return `<article class="mcp-runtime-card">
    <header class="mcp-runtime-head">
      <div><div class="mcp-record-source">${sourceBadges}</div><span class="eyebrow">RUNTIME INSTALLATION</span><h3>${escapeHtml(item.server_title || item.server_name || "")}</h3><code>${escapeHtml(item.server_name || "")}@${escapeHtml(item.server_version || "")}</code></div>
      <div class="mcp-runtime-actions"><span class="mcp-state ${mcpStatusTone(processState)}"><i></i>${escapeHtml(processState)}</span><button class="btn small" data-mcp-installation-toggle="${escapeAttr(item.installation_id || "")}" data-mcp-enabled="${desired ? "false" : "true"}" ${state.mcpBusy ? "disabled" : ""}>${desired ? "停止运行" : "允许启动"}</button><button class="btn small danger" data-mcp-delete="${escapeAttr(item.installation_id || "")}" ${state.mcpBusy ? "disabled" : ""}>删除</button></div>
    </header>
    <div class="mcp-runtime-identity"><span>package<strong>${escapeHtml(item.package_type || "")} / ${escapeHtml(item.package_identifier || "")}</strong></span><span>prefix<strong>${escapeHtml(item.name_prefix || "-")}</strong></span><span>protocol<strong>${escapeHtml(metadata.protocol_version || "未记录")}</strong></span><span>Secret<strong>${item.secrets_required ? (item.secrets_configured ? "已配置" : "缺失") : "无需"}</strong></span></div>
    <div class="mcp-runtime-state-grid"><div><span>PROCESS ALLOW</span><strong>${desired ? "允许启动" : "停止运行"}</strong><small>desired_enabled=${desired ? "true" : "false"}</small></div><div><span>RUN ALLOWED</span><strong>${item.run_allowed ? "启动条件满足" : "启动条件未满足"}</strong><small>run_allowed=${item.run_allowed ? "true" : "false"}</small></div><div><span>PROCESS STATE</span><strong>${escapeHtml(processState)}</strong><small>授权与 process 生命周期分离</small></div><div><span>TOOL CATALOG</span><strong>${Number(item.tool_count || tools.length)}</strong><small>preflight / reload catalog</small></div></div>
    <div class="mcp-runtime-counts"><span>authorized<strong>${Number(item.authorized_count || 0)}</strong></span><span>registered<strong>${Number(item.registered_count || 0)}</strong></span><span>effective<strong>${Number(item.effective_count || 0)}</strong></span><span>total<strong>${Number(item.tool_count || tools.length)}</strong></span></div>
    <p class="mcp-runtime-note">Server 停止只终止 process 并撤下注册，不会清除 tool 授权；再次允许启动后按 catalog 恢复。</p>
    ${links ? `<div class="mcp-record-links">${links}</div>` : ""}
    ${item.last_error ? `<div class="alert err">runtime diagnostic: ${escapeHtml(item.last_error)}</div>` : ""}
    <div class="mcp-tool-grid">${tools.map(tool => renderManagedMcpTool(tool, item)).join("") || '<div class="mcp-empty"><strong>没有 tool catalog</strong><span>执行 reload 或检查 Server 的 tools capability。</span></div>'}</div>
  </article>`;
}

function renderMcpRuntimeInstallations() {
  const installations = state.mcpInstallations || [];
  const totals = installations.reduce((acc, item) => {
    acc.authorized += Number(item.authorized_count || 0);
    acc.registered += Number(item.registered_count || 0);
    acc.effective += Number(item.effective_count || 0);
    return acc;
  }, {authorized:0, registered:0, effective:0});
  return `<section class="card mcp-zone mcp-runtime-zone">
    <div class="mcp-section-heading"><div><span class="eyebrow">02 / RUNTIME INSTALLATIONS</span><h2>Runtime installations</h2><p>process allow、persistent authorization、runtime registration 与 effective availability 分层展示。</p></div><button class="btn" data-mcp-reload ${state.mcpBusy ? "disabled" : ""}>重载 MCP runtime</button></div>
    <div class="mcp-runtime-overview"><span>installations<strong>${installations.length}</strong></span><span>authorized<strong>${totals.authorized}</strong></span><span>registered<strong>${totals.registered}</strong></span><span>effective<strong>${totals.effective}</strong></span></div>
    <div class="mcp-runtime-list">${installations.map(renderMcpInstallation).join("") || '<div class="mcp-empty"><strong>暂无 Runtime installation</strong><span>从 Registry detail 完成 execution preflight 后，安装会出现在这里。</span></div>'}</div>
  </section>`;
}

function mcpCommandPlan(item) {
  const type = String(item.registry_type || "").toLowerCase();
  const identity = mcpPackageIdentity(item);
  const hasArguments = (item.inputs || []).some(input => input.location === "argument");
  const tokens = type === "pypi"
    ? ["uvx", "--from", identity, item.identifier || "unknown"]
    : ["npx", "--yes", identity];
  if (hasArguments) tokens.push("[validated Registry package arguments]");
  return {launcher:type === "pypi" ? "uvx" : "npx", identity, tokens};
}

function renderMcpInstallConfirmation() {
  const pending = _mcpPendingInstall;
  if (!pending) return "";
  const plan = pending.plan;
  const inputs = pending.inputSummary.map(item => `<li><span>${escapeHtml(item.location)} / <code>${escapeHtml(item.key)}</code></span><strong>${item.secret ? (item.provided ? "已提供，值不显示" : "未提供") : (item.provided ? "已提供" : "使用 Registry default / 空值")}</strong></li>`).join("");
  return `<div class="mcp-confirm-backdrop" role="presentation"><section class="mcp-confirm-panel" role="dialog" aria-modal="true" aria-labelledby="mcp-confirm-title">
    <div class="mcp-section-heading"><div><span class="eyebrow">EXECUTION CONFIRMATION</span><h2 id="mcp-confirm-title">确认第三方 package 执行计划</h2><p>后端 detail 当前不返回解析后的绝对 command path；这里展示精确 package identity 与将由 launcher 执行的安全摘要，不伪造本机路径。</p></div><button class="btn small" data-mcp-install-cancel>取消</button></div>
    <div class="mcp-command-plan"><span>launcher</span><strong>${escapeHtml(plan.launcher)}</strong><span>exact package identity</span><code>${escapeHtml(plan.identity)}</code><span>command token plan</span><div>${plan.tokens.map(token => `<code>${escapeHtml(token)}</code>`).join('<b aria-hidden="true">→</b>')}</div><span>metadata guard</span><code>fresh_fetch=true · digest=${escapeHtml(pending.package.digest || "")}</code></div>
    <ul class="mcp-confirm-inputs">${inputs || '<li><span>inputs</span><strong>无额外参数</strong></li>'}</ul>
    <div class="alert err">Registry publisher metadata、description 与 annotations 均视为 untrusted。确认后会以 Bot 系统用户权限执行 package 并调用 initialize / tools/list 预检。</div>
    <div class="mcp-confirm-actions"><button class="btn" data-mcp-install-cancel>返回修改</button><button class="btn primary" data-mcp-install-confirm ${state.mcpBusy ? "disabled" : ""}>${state.mcpBusy ? '<span class="spinner"></span> 正在预检' : '确认执行、预检并安装'}</button></div>
  </section></div>`;
}

function renderMcp() {
  const running = (state.mcpInstallations || []).filter(item => item.process_state === "running").length;
  const effective = (state.mcpInstallations || []).reduce((sum, item) => sum + Number(item.effective_count || 0), 0);
  return `<div class="mcp-console">
    <section class="mcp-hero"><div><span class="eyebrow">MODEL CONTEXT PROTOCOL / CONTROL PLANE</span><h1>MCP 管理</h1><p>将 Registry discovery 与 Runtime installations 分离：先核验权威 metadata，再明确控制 process 与逐 tool 授权。</p></div><div class="mcp-hero-readout"><span>sources<strong>${(state.mcpSources || []).length}</strong></span><span>installations<strong>${(state.mcpInstallations || []).length}</strong></span><span>running<strong>${running}</strong></span><span>effective<strong>${effective}</strong></span></div></section>
    ${renderMcpOperationResult()}
    ${renderMcpRegistryDiscovery()}
    ${renderMcpRuntimeInstallations()}
    ${renderMcpInstallConfirmation()}
  </div>`;
}

function mergeMcpRegistryResults(current, incoming) {
  const merged = Array.isArray(current) ? current.slice() : [];
  const positions = new Map(merged.map((item, index) => [`${item.source_id || state.mcpSourceId}\u0000${item.name || ""}`, index]));
  for (const item of Array.isArray(incoming) ? incoming : []) {
    const key = `${item.source_id || state.mcpSourceId}\u0000${item.name || ""}`;
    if (positions.has(key)) merged[positions.get(key)] = {...merged[positions.get(key)], ...item};
    else {
      positions.set(key, merged.length);
      merged.push(item);
    }
  }
  return merged;
}

function replaceMcpInstallation(item) {
  if (!item || !item.installation_id) return;
  const installations = state.mcpInstallations || [];
  const index = installations.findIndex(current => current.installation_id === item.installation_id);
  if (index < 0) state.mcpInstallations = [item, ...installations];
  else state.mcpInstallations = installations.map((current, currentIndex) => currentIndex === index ? item : current);
}

async function loadMcpInstallations() {
  const data = await api("/mcp/installations");
  state.mcpInstallations = data.installations || [];
}

async function searchMcpRegistry({append=false}={}) {
  if (state.mcpBusy) return;
  const input = document.getElementById("mcp-search-input");
  const source = document.getElementById("mcp-source-select");
  if (!append) {
    state.mcpQuery = String(input ? input.value : state.mcpQuery || "").trim();
    state.mcpSourceId = String(source ? source.value : state.mcpSourceId || "official");
    state.mcpNextCursor = "";
    state.mcpDetail = null;
  }
  if (append && !state.mcpNextCursor) return;
  const params = {source_id:state.mcpSourceId, q:state.mcpQuery, limit:"30"};
  if (append) params.cursor = state.mcpNextCursor;
  const requestSourceId = state.mcpSourceId;
  const requestQuery = state.mcpQuery;
  const requestCursor = append ? state.mcpNextCursor : "";
  state.mcpBusy = true;
  state.mcpLoadingMore = append;
  render();
  try {
    const data = await api("/mcp/search?" + new URLSearchParams(params).toString());
    if (state.mcpSourceId !== requestSourceId || state.mcpQuery !== requestQuery || (append && state.mcpNextCursor !== requestCursor)) return;
    state.mcpResults = append
      ? mergeMcpRegistryResults(state.mcpResults, data.servers)
      : mergeMcpRegistryResults([], data.servers);
    state.mcpNextCursor = typeof data.next_cursor === "string" ? data.next_cursor : "";
    state.mcpSearchLoaded = true;
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP Registry 搜索失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP Registry 搜索失败");
  } finally {
    state.mcpBusy = false;
    state.mcpLoadingMore = false;
    render();
  }
}

async function openMcpDetail(name, sourceId) {
  if (!name || state.mcpBusy) return;
  state.mcpBusy = true;
  render();
  try {
    state.mcpDetail = await api("/mcp/detail?" + new URLSearchParams({source_id:sourceId || state.mcpSourceId, name}).toString());
    const first = (state.mcpDetail.packages || []).find(item => item.supported === true);
    state.mcpPackageIndex = first ? first.index : 0;
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP Server 详情读取失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP Server 详情读取失败");
  } finally {
    state.mcpBusy = false;
    render();
  }
}

function prepareMcpInstall() {
  const detail = state.mcpDetail;
  if (!detail) return;
  const selected = (detail.packages || []).find(item => Number(item.index) === Number(state.mcpPackageIndex) && item.supported === true);
  if (!selected) return;
  const inputs = {};
  const inputSummary = [];
  let missing = "";
  document.querySelectorAll("[data-mcp-install-input]").forEach(element => {
    const key = element.getAttribute("data-mcp-install-input") || "";
    const spec = (selected.inputs || []).find(item => item.key === key) || {};
    const value = String(element.value || "");
    if (key && value) inputs[key] = value;
    if (!value && spec.required && !spec.default && !missing) missing = key;
    inputSummary.push({key, location:spec.location || "input", secret:spec.secret === true, provided:Boolean(value)});
  });
  if (missing) {
    const diagnostic = {ok:false, code:"mcp_install_input_missing", phase:"client_validation", title:"MCP 安装输入不完整", message:`必填输入 ${missing} 尚未提供。`, details:[{label:"缺少字段", value:missing, status:"error"}], steps:[], warnings:[], suggestion:"补齐必填字段后重新审阅 command plan。", retryable:true, partial:false, outcome_unknown:false};
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title);
    return;
  }
  state.mcpPrefix = String(document.getElementById("mcp-prefix-input")?.value || "").trim();
  _mcpPendingInstall = {
    package:selected,
    plan:mcpCommandPlan(selected),
    inputSummary,
    payload:{
      source_id:String((detail.source || {}).id || state.mcpSourceId),
      server_name:String((detail.server || {}).name || ""),
      package_index:Number(selected.index),
      package_digest:String(selected.digest || ""),
      inputs,
      name_prefix:state.mcpPrefix,
      confirm_execution:true,
      fresh_fetch:true,
    },
  };
  render();
}

async function installMcpServer() {
  if (!_mcpPendingInstall || state.mcpBusy) return;
  const pending = _mcpPendingInstall;
  state.mcpBusy = true;
  render();
  try {
    const result = await api("/mcp/install", {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(pending.payload)});
    persistMcpOperationResult(result);
    replaceMcpInstallation(result.installation);
    state.mcpDetail = null;
    state.mcpPrefix = "";
    alertFlash("ok", "MCP Server 已完成预检并安装");
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP Server 安装失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP Server 安装失败");
  } finally {
    _mcpPendingInstall = null;
    state.mcpBusy = false;
    render();
  }
}

async function toggleMcpInstallation(installationId, enabled) {
  if (!installationId || state.mcpBusy) return;
  state.mcpBusy = true;
  render();
  try {
    const result = await api(`/mcp/installations/${encodeURIComponent(installationId)}/toggle`, {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({enabled})});
    persistMcpOperationResult(result);
    replaceMcpInstallation(result.installation);
    alertFlash("ok", enabled ? "Server 已允许启动" : "Server 已停止运行，工具授权保留");
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP Server 状态切换失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP Server 状态切换失败");
  } finally {
    state.mcpBusy = false;
    render();
  }
}

async function toggleManagedMcpTool(installationId, remoteName, enabled, risk) {
  if (!installationId || !remoteName || state.mcpBusy) return;
  if (enabled) {
    const riskText = risk === "read"
      ? "publisher 的 readOnlyHint 是未受信任声明，不能作为安全保证。"
      : "publisher 未提供可信只读保证，副作用按 unknown 处理。";
    if (!confirm(`${riskText}\n授权后仅在 Server 运行且 tool 已注册时才可调用。确认授权？`)) return;
  }
  state.mcpBusy = true;
  render();
  try {
    const result = await api(`/mcp/installations/${encodeURIComponent(installationId)}/tools/${encodeURIComponent(remoteName)}/toggle`, {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({enabled, confirm_side_effect:enabled})});
    persistMcpOperationResult(result);
    replaceMcpInstallation(result.installation);
    alertFlash("ok", enabled ? "MCP tool 已授权" : "MCP tool 已撤销授权");
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP 工具授权切换失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP 工具授权切换失败");
  } finally {
    state.mcpBusy = false;
    render();
  }
}

async function deleteMcpInstallation(installationId) {
  if (!installationId || state.mcpBusy) return;
  if (!confirm("确认删除这个 MCP installation、tool policy 与 Secret 文件条目？该操作不会保留授权。")) return;
  state.mcpBusy = true;
  render();
  try {
    await api(`/mcp/installations/${encodeURIComponent(installationId)}`, {method:"DELETE", headers:{"content-type":"application/json"}, body:JSON.stringify({confirm:"delete"})});
    persistMcpOperationResult({ok:true, code:"mcp_installation_deleted", phase:"delete", title:"MCP installation 已删除", message:"process、tool policy 与独立 Secret 条目已由服务端删除。", details:[{label:"installation_id", value:installationId, status:"ok"}], steps:[{key:"delete", label:"删除 installation", status:"ok", message:"服务端返回明确成功。"}], warnings:[], suggestion:"", retryable:false, partial:false, outcome_unknown:false, operation_id:installationId});
    state.mcpInstallations = (state.mcpInstallations || []).filter(item => item.installation_id !== installationId);
    alertFlash("ok", "MCP installation 已删除");
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP Server 删除失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP Server 删除失败");
  } finally {
    state.mcpBusy = false;
    render();
  }
}

async function reloadMcpRuntime() {
  if (state.mcpBusy) return;
  state.mcpBusy = true;
  render();
  try {
    const result = await api("/mcp/reload", {method:"POST"});
    persistMcpOperationResult(result);
    let refreshFailed = false;
    try {
      await loadMcpInstallations();
    } catch (refreshError) {
      refreshFailed = true;
      const refreshDiagnostic = operationDiagnosticFromError(refreshError, "MCP reload 后状态刷新失败");
      alertFlash("err", refreshDiagnostic.title || "MCP reload 后状态刷新失败");
    }
    if (!refreshFailed) alertFlash(result.diagnostic && result.diagnostic.ok === false ? "err" : "ok", result.diagnostic?.title || "MCP runtime 已重载");
  } catch (error) {
    const diagnostic = operationDiagnosticFromError(error, "MCP reload 失败");
    persistMcpOperationResult(diagnostic);
    alertFlash("err", diagnostic.title || "MCP reload 失败");
  } finally {
    state.mcpBusy = false;
    render();
  }
}

if (!window.__personificationMcpPageEvents) {
  window.__personificationMcpPageEvents = true;
  document.addEventListener("click", event => {
    const element = event.target instanceof Element ? event.target.closest("[data-mcp-operation-clear],[data-mcp-search],[data-mcp-load-more],[data-mcp-detail],[data-mcp-detail-close],[data-mcp-install-plan],[data-mcp-install-confirm],[data-mcp-install-cancel],[data-mcp-installation-toggle],[data-mcp-tool-toggle],[data-mcp-delete],[data-mcp-reload]") : null;
    if (!element) return;
    if (element.hasAttribute("data-mcp-operation-clear")) { persistMcpOperationResult(null); render(); return; }
    if (element.hasAttribute("data-mcp-search")) { searchMcpRegistry(); return; }
    if (element.hasAttribute("data-mcp-load-more")) { searchMcpRegistry({append:true}); return; }
    if (element.hasAttribute("data-mcp-detail")) { openMcpDetail(element.getAttribute("data-mcp-detail") || "", element.getAttribute("data-mcp-source") || state.mcpSourceId); return; }
    if (element.hasAttribute("data-mcp-detail-close")) { state.mcpDetail = null; _mcpPendingInstall = null; render(); return; }
    if (element.hasAttribute("data-mcp-install-plan")) { prepareMcpInstall(); return; }
    if (element.hasAttribute("data-mcp-install-confirm")) { installMcpServer(); return; }
    if (element.hasAttribute("data-mcp-install-cancel")) { _mcpPendingInstall = null; render(); return; }
    if (element.hasAttribute("data-mcp-installation-toggle")) { toggleMcpInstallation(element.getAttribute("data-mcp-installation-toggle") || "", element.getAttribute("data-mcp-enabled") === "true"); return; }
    if (element.hasAttribute("data-mcp-tool-toggle")) { toggleManagedMcpTool(element.getAttribute("data-mcp-installation") || "", element.getAttribute("data-mcp-tool-toggle") || "", element.getAttribute("data-mcp-enabled") === "true", element.getAttribute("data-mcp-risk") || "unknown"); return; }
    if (element.hasAttribute("data-mcp-delete")) { deleteMcpInstallation(element.getAttribute("data-mcp-delete") || ""); return; }
    if (element.hasAttribute("data-mcp-reload")) reloadMcpRuntime();
  });
  document.addEventListener("change", event => {
    const element = event.target;
    if (!(element instanceof Element)) return;
    if (element.id === "mcp-source-select") {
      state.mcpSourceId = String(element.value || "official");
      state.mcpResults = [];
      state.mcpNextCursor = "";
      state.mcpSearchLoaded = false;
      state.mcpDetail = null;
      render();
    } else if (element.id === "mcp-package-select") {
      state.mcpPackageIndex = Number(element.value || 0);
      _mcpPendingInstall = null;
      render();
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Enter" && event.target instanceof Element && event.target.id === "mcp-search-input") {
      event.preventDefault();
      searchMcpRegistry();
    }
  });
}
