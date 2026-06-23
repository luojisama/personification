function renderSkills() {
  if (state.skillsAvailable === false) return `<div class="card muted">tool_registry 未就绪</div>${renderRemoteSkillSources()}${renderMcpTools()}`;
  const search = (state.skillFilter || "").trim().toLowerCase();
  const items = search ? state.skills.filter(s => {
    const hay = [s.name, s.description, s.category, s.source_kind, s.mcp ? "mcp" : ""].join(" ").toLowerCase();
    return hay.includes(search);
  }) : state.skills;
  const rows = items.map(s => {
    const active = s.enabled_by_config && !s.user_disabled;
    const tags = [
      s.category ? `<span class="tag">${escapeHtml(s.category)}</span>` : "",
      s.source_kind ? `<span class="tag">${escapeHtml(s.source_kind)}</span>` : "",
      s.mcp ? '<span class="tag source-runtime_config">MCP</span>' : "",
      s.local === false && !s.mcp ? '<span class="tag">remote</span>' : "",
    ].filter(Boolean).join("");
    return `<tr>
      <td><strong>${escapeHtml(s.name)}</strong><div style="margin-top:4px">${tags}</div></td>
      <td class="muted" style="font-size:12.5px">${escapeHtml((s.description||"").slice(0,140))}</td>
      <td>${active ? '<span class="tag" style="background:rgba(52,211,153,0.18);color:var(--ok)">启用</span>' : '<span class="tag" style="background:rgba(248,113,113,0.18);color:var(--danger)">禁用</span>'}</td>
      <td>
        <div class="toggle">
          <button class="${!s.user_disabled?'on':''}" onclick="toggleSkill('${escapeAttr(s.name)}', false)">开</button>
          <button class="${s.user_disabled?'on':''}" onclick="toggleSkill('${escapeAttr(s.name)}', true)">关</button>
        </div>
      </td>
    </tr>`;
  }).join("");
  return `${renderSkillSummary()}
    <div class="toolbar">
      <input id="skill-filter-input" type="search" placeholder="搜索 skill 名称…" value="${escapeAttr(state.skillFilter)}" oninput="state.skillFilter=this.value;render()" style="flex:1;max-width:340px">
      <span class="muted">共 ${state.skills.length} 个 skill</span>
      <button class="btn" onclick="reloadSkillRuntime()" ${state.skillSummary && state.skillSummary.reload_available ? "" : "disabled"}>重载 Skill</button>
    </div>
    ${renderRemoteSkillSources()}
    ${renderMcpTools()}
    <div class="card"><h2>Skill 启停</h2>
      <div class="table-wrap"><table><thead><tr><th>名称</th><th>说明</th><th>状态</th><th>开关</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">无 skill</td></tr>'}</tbody></table></div>
    </div>`;
}

function renderSkillSummary() {
  const s = state.skillSummary || {};
  const remoteStatus = s.remote_enabled
    ? '<span class="device-status approved">已开</span>'
    : '<span class="device-status pending">关闭</span>';
  const unsafe = s.allow_unsafe_external
    ? '<span class="tag required">允许非隔离</span>'
    : '<span class="tag">隔离优先</span>';
  const review = s.require_admin_review
    ? '<span class="tag source-runtime_config">需审核</span>'
    : '<span class="tag required">免审核</span>';
  return `<div class="skill-summary">
    <div class="skill-stat"><span class="muted">可用工具</span><strong>${Number(s.active || 0)}</strong><small>/ ${Number(s.total || 0)}</small></div>
    <div class="skill-stat"><span class="muted">用户禁用</span><strong>${Number(s.user_disabled || 0)}</strong></div>
    <div class="skill-stat"><span class="muted">远程源</span><strong>${Number(s.remote_sources_enabled || 0)}</strong><small>${remoteStatus}</small></div>
    <div class="skill-stat"><span class="muted">待审核</span><strong>${Number(s.remote_pending || 0)}</strong><small>${review}</small></div>
    <div class="skill-stat"><span class="muted">MCP</span><strong>${Number(s.mcp_tools || 0)}</strong><small>stdio</small></div>
    <div class="skill-stat"><span class="muted">外部执行</span><strong style="font-size:13px">${unsafe}</strong></div>
  </div>`;
}

function _remoteStatusTag(status) {
  const st = String(status || "pending").toLowerCase();
  if (st === "approved") return '<span class="device-status approved">通过</span>';
  if (st === "rejected") return '<span class="device-status pending" style="background:rgba(248,113,113,0.18);color:var(--danger)">拒绝</span>';
  if (st === "disabled") return '<span class="tag">未启用</span>';
  return '<span class="device-status pending">待审</span>';
}

function renderRemoteSkillSources() {
  const sources = state.skillRemoteSources || [];
  const s = state.skillSummary || {};
  const rows = sources.map(item => {
    const selector = item.key || item.name || item.source;
    return `<tr>
      <td><strong>${escapeHtml(item.name || ("source_" + (item.index + 1)))}</strong><br><code style="font-size:11px">${escapeHtml(item.key || "")}</code></td>
      <td style="word-break:break-all">${escapeHtml(item.source || "")}${item.ref ? `<br><span class="muted">ref=${escapeHtml(item.ref)}</span>` : ""}${item.subdir ? `<br><span class="muted">subdir=${escapeHtml(item.subdir)}</span>` : ""}</td>
      <td>${_remoteStatusTag(item.status)}</td>
      <td>
        <div class="row" style="gap:6px">
          <button class="btn small primary" onclick="reviewRemoteSkill('${escapeAttr(selector)}','approved')" ${item.status==="approved"?"disabled":""}>批准</button>
          <button class="btn small" onclick="reviewRemoteSkill('${escapeAttr(selector)}','pending')">待审</button>
          <button class="btn small danger" onclick="reviewRemoteSkill('${escapeAttr(selector)}','rejected')">拒绝</button>
        </div>
      </td>
    </tr>`;
  }).join("");
  return `<div class="card">
    <div class="between" style="gap:12px;align-items:flex-start">
      <div><h2>远程 Skill 源</h2>
        <div class="row" style="gap:6px">
          ${s.remote_enabled ? '<span class="device-status approved">远程加载已开</span>' : '<span class="device-status pending">远程加载关闭</span>'}
          ${s.require_admin_review ? '<span class="tag source-runtime_config">管理员审核</span>' : '<span class="tag required">免审核</span>'}
          ${s.allow_unsafe_external ? '<span class="tag required">允许非隔离外部代码</span>' : '<span class="tag">非隔离外部代码关闭</span>'}
        </div>
      </div>
      <button class="btn small" onclick="setSkillRemoteEnabled(${s.remote_enabled ? "false" : "true"})">${s.remote_enabled ? "关闭远程" : "开启远程"}</button>
    </div>
    <div class="remote-source-form">
      <input id="skill-source-url" placeholder="GitHub / zip / 本地目录" value="${escapeAttr(state.skillSourceForm.source || "")}" oninput="state.skillSourceForm.source=this.value">
      <input id="skill-source-name" placeholder="名称" value="${escapeAttr(state.skillSourceForm.name || "")}" oninput="state.skillSourceForm.name=this.value">
      <input id="skill-source-ref" placeholder="ref" value="${escapeAttr(state.skillSourceForm.ref || "")}" oninput="state.skillSourceForm.ref=this.value">
      <input id="skill-source-subdir" placeholder="subdir" value="${escapeAttr(state.skillSourceForm.subdir || "")}" oninput="state.skillSourceForm.subdir=this.value">
      <label><input type="checkbox" ${state.skillSourceForm.preferFirst ? "checked" : ""} onchange="state.skillSourceForm.preferFirst=this.checked"> 优先</label>
      <label><input type="checkbox" ${state.skillSourceForm.autoApprove ? "checked" : ""} onchange="state.skillSourceForm.autoApprove=this.checked"> 添加后批准</label>
      <button class="btn primary" onclick="addRemoteSkillSource()">添加源</button>
    </div>
    <div class="table-wrap"><table><thead><tr><th>名称</th><th>来源</th><th>审核</th><th>操作</th></tr></thead><tbody>${rows || '<tr><td colspan="4" class="muted">暂无远程源</td></tr>'}</tbody></table></div>
  </div>`;
}

function renderMcpTools() {
  const tools = state.skillMcpTools || [];
  const rows = tools.map(t => `<tr>
    <td><strong>${escapeHtml(t.name)}</strong><br><span class="muted">remote=${escapeHtml(t.remote_name || "-")}</span></td>
    <td><code>${escapeHtml(t.command || "-")}</code></td>
    <td class="muted">${escapeHtml(t.cwd || "-")}</td>
    <td>${Number(t.timeout || 0)}s</td>
    <td>${Number(t.args_count || 0)} / ${Number(t.env_count || 0)}</td>
  </tr>`).join("");
  return `<div class="card">
    <h2>MCP 工具</h2>
    <div class="table-wrap"><table><thead><tr><th>工具</th><th>命令</th><th>cwd</th><th>超时</th><th>args/env</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="muted">当前未注册 MCP stdio 工具</td></tr>'}</tbody></table></div>
  </div>`;
}

async function toggleSkill(name, disabled) {
  try {
    await api(`/skills/${encodeURIComponent(name)}/toggle`, { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({disabled}) });
    alertFlash("ok", `${name} 已${disabled?'禁用':'启用'}`);
    await loadView(); render();
  } catch (e) { alertFlash("err", "切换失败：" + e.message); }
}

async function setSkillRemoteEnabled(enabled) {
  try {
    await api("/config/value", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({field_name:"personification_skill_remote_enabled", value: !!enabled}) });
    alertFlash("ok", enabled ? "远程 Skill 已开启" : "远程 Skill 已关闭");
    await loadView(); render();
  } catch (e) { alertFlash("err", "保存失败：" + e.message); }
}

async function addRemoteSkillSource() {
  const f = state.skillSourceForm || {};
  if (!String(f.source || "").trim()) { alertFlash("err", "source 不能为空"); return; }
  try {
    const payload = {
      source: f.source, name: f.name, ref: f.ref, subdir: f.subdir, kind: f.kind || "auto",
      prefer_first: !!f.preferFirst, auto_approve: !!f.autoApprove, enable_remote: true,
    };
    const result = await api("/skills/remote/source", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify(payload) });
    state.skillSourceForm = { source: "", name: "", ref: "", subdir: "", kind: "auto", preferFirst: false, autoApprove: false };
    alertFlash("ok", result.auto_approved ? "远程源已添加并批准，重载后生效" : "远程源已添加，审核后重载生效");
    await loadView(); render();
  } catch (e) { alertFlash("err", "添加失败：" + e.message); }
}

async function reviewRemoteSkill(selector, status) {
  try {
    const result = await api("/skills/remote/review", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({selector, status}) });
    alertFlash("ok", `已更新 ${result.matched_count || 0} 个远程源`);
    await loadView(); render();
  } catch (e) { alertFlash("err", "审核失败：" + e.message); }
}

async function reloadSkillRuntime() {
  try {
    await api("/skills/reload", { method:"POST" });
    alertFlash("ok", "Skill 运行时已重载");
    await loadView(); render();
  } catch (e) { alertFlash("err", "重载失败：" + e.message); }
}

function renderPluginKnowledge() {
  if (state.pluginKnowledgeAvailable === false) return `<div class="card muted">knowledge_store 未就绪</div>`;
  if (state.selectedPluginKnowledge) return renderPluginKnowledgeDetail();
  const list = state.pluginKnowledgeList || [];
  const searchResults = state.pluginKnowledgeSearchResults;
  const matchedSet = searchResults ? new Set(searchResults.results || []) : null;
  const displayList = matchedSet ? list.filter(p => matchedSet.has(p.plugin_name)) : list;
  const rows = displayList.map(p => `<tr>
    <td><strong>${escapeHtml(p.display_name || p.plugin_name)}</strong>${p.category ? ` <span class="tag">${escapeHtml(p.category)}</span>` : ''}</td>
    <td><code>${escapeHtml(p.plugin_name)}</code></td>
    <td>${escapeHtml(p.summary || '')}</td>
    <td class="muted" style="font-size:12px">
      ${p.has_runtime_data ? '<span class="tag">runtime</span>' : ''}
      ${p.has_source_data ? `<span class="tag">source(${p.source_file_count}f/${p.source_chunk_count}c)</span>` : ''}
    </td>
    <td><button class="btn small" onclick="openPluginKnowledge('${escapeAttr(p.plugin_name)}')">详情</button></td>
  </tr>`).join("");
  const searchInfo = matchedSet ? `<div class="muted" style="margin-bottom:8px">搜索 "${escapeHtml(state.pluginKnowledgeSearchQ || '')}" 命中 ${matchedSet.size} 条 <button class="btn small" onclick="clearPluginKnowledgeSearch()">清除</button></div>` : '';
  return `<div class="card">
    <div class="row" style="margin-bottom:12px;gap:8px;align-items:center">
      <input id="pk-search-input" placeholder="按插件名/关键词/摘要搜索" value="${escapeAttr(state.pluginKnowledgeSearchQ || '')}" onkeydown="if(event.key==='Enter')triggerPluginKnowledgeSearch()" style="flex:1">
      <button class="btn" onclick="triggerPluginKnowledgeSearch()">搜索</button>
    </div>
    ${searchInfo}
    <h2>插件知识库（${displayList.length} / ${list.length}）</h2>
    <table><thead><tr><th>名称</th><th>plugin_name</th><th>摘要</th><th>数据</th><th></th></tr></thead><tbody>${rows||'<tr><td colspan="5" class="muted">暂无插件知识，等待自动构建或手动触发。</td></tr>'}</tbody></table>
  </div>`;
}

async function triggerPluginKnowledgeSearch() {
  const input = document.getElementById("pk-search-input");
  const q = (input ? input.value : "").trim();
  state.pluginKnowledgeSearchQ = q;
  if (!q) { state.pluginKnowledgeSearchResults = null; render(); return; }
  try {
    state.pluginKnowledgeSearchResults = await api("/plugin-knowledge/search?" + new URLSearchParams({q, top_k: "30"}).toString());
    render();
  } catch (e) { alertFlash("err", e.message); }
}

function clearPluginKnowledgeSearch() {
  state.pluginKnowledgeSearchQ = "";
  state.pluginKnowledgeSearchResults = null;
  render();
}

async function openPluginKnowledge(name) {
  try {
    state.selectedPluginKnowledge = await api("/plugin-knowledge/detail/" + encodeURIComponent(name));
    render();
  } catch (e) { alertFlash("err", e.message); }
}

function renderPluginKnowledgeDetail() {
  const d = state.selectedPluginKnowledge;
  const e = d.entry || {};
  const features = Array.isArray(e.features) ? e.features : [];
  const featureRows = features.map(f => {
    if (typeof f === "string") return `<li>${escapeHtml(f)}</li>`;
    const name = f.name || f.feature || "";
    const desc = f.description || f.desc || "";
    return `<li><strong>${escapeHtml(name)}</strong>${desc ? `：${escapeHtml(desc)}` : ''}</li>`;
  }).join("");
  return `<div class="row" style="margin-bottom:10px"><button class="btn small" onclick="state.selectedPluginKnowledge=null;render()">返回列表</button><span class="muted">插件 ${escapeHtml(d.plugin_name)}</span></div>
    <div class="card">
      <h2>${escapeHtml(e.display_name || d.plugin_name)} <code style="font-size:13px;color:var(--muted)">${escapeHtml(d.plugin_name)}</code></h2>
      ${e.summary ? `<p>${escapeHtml(e.summary)}</p>` : ''}
      ${(e.keywords && e.keywords.length) ? `<div style="margin:6px 0">${e.keywords.map(k => `<span class="tag">${escapeHtml(k)}</span>`).join("")}</div>` : ''}
      ${e.architecture_summary ? `<h3>架构摘要</h3><pre style="white-space:pre-wrap;margin:0;font-family:inherit">${escapeHtml(e.architecture_summary)}</pre>` : ''}
      ${features.length ? `<h3>功能列表</h3><ul>${featureRows}</ul>` : ''}
      <details style="margin-top:12px"><summary class="muted">完整 JSON</summary><pre style="white-space:pre-wrap;font-size:12px;background:#0b0d12;padding:10px;border-radius:6px;overflow-x:auto">${escapeHtml(JSON.stringify(e, null, 2))}</pre></details>
    </div>`;
}

function renderTest() {
  const r = state.testResult;
  return `<div class="card">
    <h2>模型调用测试</h2>
    <label class="muted">system prompt</label>
    <textarea oninput="state.testSystem=this.value" style="width:100%;min-height:60px;margin:6px 0">${escapeHtml(state.testSystem)}</textarea>
    <label class="muted">用户消息</label>
    <textarea oninput="state.testPrompt=this.value" style="width:100%;min-height:80px;margin:6px 0">${escapeHtml(state.testPrompt)}</textarea>
    <div class="row" style="margin-top:10px">
      <button class="btn primary" onclick="runTest()">发送（路由模型）</button>
      <button class="btn" onclick="runTestAll()">测试全部 provider</button>
      ${state.testLoading?'<span class="muted">调用中…</span>':''}
    </div>
    <p class="muted" style="margin-top:8px;font-size:12px">“测试全部 provider”会向 api_pools 里每个 provider 各发一次，分别返回延迟与内容，用于排查哪个供应商不通或被拦截。</p>
  </div>
  ${r ? `<div class="card"><h2>响应（路由模型）</h2>
    <div class="row muted" style="font-size:12px;margin-bottom:8px">
      <span>模型 <code>${escapeHtml(r.model_used||'未知')}</code></span>
      <span>finish=${escapeHtml(r.finish_reason||'')}</span>
      <span>${r.duration_ms}ms</span>
      <span>tokens prompt=${r.usage?.prompt_tokens||0} completion=${r.usage?.completion_tokens||0}</span>
    </div>
    <pre style="white-space:pre-wrap;margin:0;font-family:inherit">${escapeHtml(r.content||'(无内容)')}</pre>
  </div>` : ''}
  ${renderTestAll()}`;
}

function renderTestAll() {
  const ra = state.testAllResult;
  if (!ra) return '';
  const rows = (ra.results || []).slice().sort((a,b)=>(a.priority-b.priority)||(a.index-b.index)).map(x => {
    const ok = x.ok && !x.error;
    const status = ok
      ? '<span class="device-status approved">通过</span>'
      : (x.blocked_reason ? '<span class="device-status pending">被拦截</span>'
                          : '<span class="device-status pending" style="background:rgba(248,113,113,0.18);color:var(--danger)">失败</span>');
    const detail = ok ? (escapeHtml((x.content||'').slice(0,200)) || '(空)') : escapeHtml(x.error || x.blocked_reason || '未知错误');
    return `<tr>
      <td>${escapeHtml(x.name||'')}</td>
      <td class="muted">${escapeHtml(x.api_type||'')} / ${escapeHtml(x.model||'')}</td>
      <td>${status}</td>
      <td>${x.duration_ms!=null?x.duration_ms+'ms':'-'}</td>
      <td style="max-width:380px;white-space:pre-wrap;word-break:break-word">${detail}</td>
    </tr>`;
  }).join("");
  return `<div class="card"><h2>全部 provider 测试（${ra.count||0}）</h2>
    <table><thead><tr><th>名称</th><th>类型 / 模型</th><th>状态</th><th>延迟</th><th>内容 / 错误</th></tr></thead>
    <tbody>${rows||'<tr><td colspan="5" class="muted">无</td></tr>'}</tbody></table>
  </div>`;
}

async function runTest() {
  state.testLoading = true; render();
  try {
    state.testResult = await api("/test/chat", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({prompt: state.testPrompt, system: state.testSystem}) });
  } catch (e) { alertFlash("err", "调用失败：" + e.message); }
  state.testLoading = false; render();
}

async function runTestAll() {
  state.testLoading = true; render();
  try {
    state.testAllResult = await api("/test/chat-all", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({prompt: state.testPrompt, system: state.testSystem}) });
  } catch (e) { alertFlash("err", "测试失败：" + e.message); }
  state.testLoading = false; render();
}

function renderPersonaPrompt() {
  const p = state.personaPrompt;
  const meta = p ? `<div class="row muted" style="font-size:12px;margin-bottom:8px;gap:14px">
      <span>来源：${escapeHtml(p.source||'-')}</span>
      ${p.resolved_path ? `<span>路径：<code>${escapeHtml(p.resolved_path)}</code></span>` : ''}
      <span>${p.exists ? (p.is_file ? (p.size+' 字节') : '内联文本') : '<span style="color:var(--danger)">文件不存在</span>'}</span>
    </div>` : '';
  const body = p && (p.content || p.content === '')
    ? `<pre style="white-space:pre-wrap;margin:0;font-family:ui-monospace,Consolas,monospace;max-height:60vh;overflow:auto">${escapeHtml(p.content || '(空)')}</pre>`
    : '<p class="muted">加载中…</p>';
  return `<div class="card">
    <h2>人设预览</h2>
    <p class="muted" style="font-size:12.5px">默认显示当前生效的人设文件（prompt_path / system_path / system_prompt）。也可输入任意路径查看其内容。</p>
    <div class="row" style="margin:10px 0">
      <input id="persona-path" type="text" placeholder="留空=当前配置；或输入文件路径" value="${escapeAttr(state.personaPromptPath||'')}" style="flex:1;min-width:240px" onkeydown="if(event.key==='Enter')loadPersonaPrompt()">
      <button class="btn primary" onclick="loadPersonaPrompt()">查看</button>
      ${state.personaPromptPath ? '<button class="btn" onclick="resetPersonaPrompt()">重置为当前配置</button>' : ''}
    </div>
  </div>
  <div class="card">${meta}${body}</div>`;
}

async function loadPersonaPrompt() {
  const el = document.getElementById("persona-path");
  if (el) state.personaPromptPath = el.value.trim();
  try { await loadView(); render(); } catch (e) { alertFlash("err", "读取失败：" + e.message); }
}

async function resetPersonaPrompt() {
  state.personaPromptPath = "";
  try { await loadView(); render(); } catch (e) { alertFlash("err", "读取失败：" + e.message); }
}
