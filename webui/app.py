from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from .routes.auth_routes import build_auth_router
from .routes.config_routes import build_config_router


@dataclass
class _RuntimeContext:
    plugin_config: Any
    superusers: set[str]
    get_bots: Callable[[], dict[str, Any]]
    logger: Any


_RUNTIME: _RuntimeContext | None = None


def set_runtime_context(
    *,
    plugin_config: Any,
    superusers: set[str],
    get_bots: Callable[[], dict[str, Any]],
    logger: Any,
) -> None:
    global _RUNTIME
    _RUNTIME = _RuntimeContext(
        plugin_config=plugin_config,
        superusers=set(superusers or set()),
        get_bots=get_bots,
        logger=logger,
    )


def get_runtime_context() -> _RuntimeContext:
    if _RUNTIME is None:
        raise RuntimeError("WebUI runtime context 未初始化")
    return _RUNTIME


def build_router() -> APIRouter:
    runtime = get_runtime_context()
    router = APIRouter(prefix="/personification")
    router.include_router(build_auth_router(runtime=runtime))
    router.include_router(build_config_router(runtime=runtime))

    @router.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_HTML)

    @router.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return router


_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>拟人插件 控制台</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#262a33; --text:#e6e8ef; --muted:#8a91a3; --accent:#6aa8ff; --danger:#f87171; --warn:#f59e0b; --ok:#34d399; }
* { box-sizing:border-box; }
html,body { margin:0; padding:0; background:var(--bg); color:var(--text); font:14px/1.55 -apple-system,Segoe UI,Roboto,"Noto Sans CJK SC","Microsoft YaHei",sans-serif; }
a { color:var(--accent); text-decoration:none; }
button { font:inherit; cursor:pointer; }
input,select,textarea { font:inherit; background:#0b0d12; color:var(--text); border:1px solid var(--line); border-radius:6px; padding:6px 10px; }
input:focus, textarea:focus, select:focus { outline:none; border-color:var(--accent); }
.layout { display:grid; grid-template-columns:220px 1fr; min-height:100vh; }
aside { background:var(--panel); border-right:1px solid var(--line); padding:18px 0; }
aside h1 { font-size:14px; padding:0 18px 14px; color:var(--muted); margin:0; letter-spacing:1px; border-bottom:1px solid var(--line); }
aside nav { display:flex; flex-direction:column; padding:10px 0; }
aside nav a { padding:9px 18px; color:var(--text); border-left:3px solid transparent; }
aside nav a.active { background:#1f242c; border-left-color:var(--accent); }
main { padding:22px 28px; }
.row { display:flex; gap:14px; align-items:center; }
.between { display:flex; justify-content:space-between; align-items:center; }
.muted { color:var(--muted); }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:18px 20px; margin-bottom:16px; }
.card h2 { margin:0 0 14px; font-size:16px; font-weight:600; }
.tag { display:inline-block; padding:1px 8px; border-radius:99px; font-size:12px; background:#222; color:var(--muted); margin-right:6px; }
.tag.required { background:rgba(248,113,113,0.18); color:var(--danger); }
.tag.secret { background:rgba(245,158,11,0.18); color:var(--warn); }
.tag.source-env_file { background:rgba(106,168,255,0.18); color:var(--accent); }
.tag.source-env_json { background:rgba(52,211,153,0.18); color:var(--ok); }
.tag.source-runtime_config { background:rgba(245,158,11,0.18); color:var(--warn); }
.tag.source-default { background:#222; color:var(--muted); }
.field { padding:14px 0; border-top:1px solid var(--line); }
.field:first-child { border-top:none; }
.field-head { display:flex; align-items:center; flex-wrap:wrap; gap:6px; margin-bottom:4px; }
.field-head strong { font-size:14px; }
.field-head code { font-size:11px; color:var(--muted); background:#0b0d12; padding:2px 6px; border-radius:4px; }
.field-desc { color:var(--muted); margin:4px 0 8px; font-size:12.5px; }
.field-input { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.field-input input[type=text], .field-input input[type=number], .field-input textarea { min-width:260px; flex:1; max-width:560px; }
.field-input textarea { min-height:60px; font-family:ui-monospace,Consolas,monospace; }
.btn { padding:7px 14px; border-radius:6px; border:1px solid var(--line); background:#1d212a; color:var(--text); }
.btn:hover { border-color:var(--accent); }
.btn.primary { background:var(--accent); color:#0b0d12; border-color:transparent; }
.btn.danger { background:transparent; color:var(--danger); border-color:rgba(248,113,113,0.4); }
.btn.small { padding:3px 9px; font-size:12px; }
.toggle { display:inline-flex; gap:4px; padding:3px; background:#0b0d12; border:1px solid var(--line); border-radius:99px; }
.toggle button { padding:4px 14px; border:none; border-radius:99px; background:transparent; color:var(--muted); }
.toggle button.on { background:var(--accent); color:#0b0d12; }
.alert { padding:10px 14px; border-radius:6px; margin-bottom:12px; }
.alert.ok { background:rgba(52,211,153,0.14); color:var(--ok); }
.alert.err { background:rgba(248,113,113,0.14); color:var(--danger); }
.alert.info { background:rgba(106,168,255,0.14); color:var(--accent); }
.group-bar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
.group-bar button { padding:5px 12px; border-radius:99px; border:1px solid var(--line); background:transparent; color:var(--muted); }
.group-bar button.active { background:var(--accent); color:#0b0d12; border-color:transparent; }
.login-wrap { max-width:380px; margin:80px auto 0; }
.login-wrap .card { padding:28px; }
.login-wrap h2 { margin:0 0 16px; }
.login-wrap label { display:block; margin:10px 0 6px; color:var(--muted); }
.login-wrap input { width:100%; }
table { width:100%; border-collapse:collapse; }
th, td { padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }
th { color:var(--muted); font-weight:500; font-size:12px; }
</style>
</head>
<body>
<div id="app"></div>
<script>
const API = "/personification/api";
let state = { logged: false, qq: "", view: "config", entries: [], groups: [], activeGroup: null, devices: [], alert: null };

async function api(path, opts = {}) {
  const res = await fetch(API + path, { credentials: "include", ...opts });
  if (res.status === 401) { state.logged = false; render(); throw new Error("未登录"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : await res.json();
}

function alertFlash(kind, text) { state.alert = { kind, text }; render(); setTimeout(() => { state.alert = null; render(); }, 4000); }

async function bootstrap() {
  try { const me = await api("/auth/me"); state.logged = true; state.qq = me.qq; await loadView(); }
  catch { state.logged = false; }
  render();
}

async function loadView() {
  if (state.view === "config") {
    const data = await api("/config/entries");
    state.entries = data.entries; state.groups = data.groups;
    if (!state.activeGroup || !state.groups.includes(state.activeGroup)) state.activeGroup = state.groups[0] || null;
  } else if (state.view === "devices") {
    const data = await api("/auth/devices");
    state.devices = data.devices; state.currentDeviceId = data.current_device_id;
  }
}

function render() {
  const root = document.getElementById("app");
  if (!state.logged) { root.innerHTML = renderLogin(); attachLogin(); return; }
  root.innerHTML = renderLayout();
  attachLayout();
}

function renderLayout() {
  const navItem = (v, label) => `<a href="#${v}" class="${state.view===v?'active':''}">${label}</a>`;
  return `<div class="layout">
    <aside>
      <h1>拟人插件控制台</h1>
      <nav>
        ${navItem('dashboard','仪表盘')}
        ${navItem('config','配置中心')}
        ${navItem('personas','用户画像')}
        ${navItem('groups','群信息')}
        ${navItem('memory','Agent 记忆')}
        ${navItem('skills','Skill 管理')}
        ${navItem('test','模型测试')}
        ${navItem('devices','设备管理')}
      </nav>
    </aside>
    <main>
      ${state.alert ? `<div class="alert ${state.alert.kind}">${escapeHtml(state.alert.text)}</div>` : ''}
      <div class="between" style="margin-bottom:14px">
        <div class="muted">登录 QQ：${escapeHtml(state.qq)}</div>
        <button class="btn small" onclick="doLogout()">退出登录</button>
      </div>
      ${renderView()}
    </main>
  </div>`;
}

function renderView() {
  if (state.view === "config") return renderConfig();
  if (state.view === "devices") return renderDevices();
  return `<div class="card"><h2>${escapeHtml(viewTitle())}</h2><p class="muted">该视图在后续版本上线（M4-M5）。</p></div>`;
}

function viewTitle() {
  return ({dashboard:"仪表盘",personas:"用户画像",groups:"群信息",memory:"Agent 记忆",skills:"Skill 管理",test:"模型测试"})[state.view] || state.view;
}

function renderConfig() {
  const groupBar = state.groups.map(g => `<button class="${g===state.activeGroup?'active':''}" onclick="pickGroup('${escapeAttr(g)}')">${escapeHtml(g)}</button>`).join("");
  const items = state.entries.filter(e => e.group === state.activeGroup);
  return `<div class="group-bar">${groupBar}</div>
    <div class="card">
      <h2>${escapeHtml(state.activeGroup || '配置')}</h2>
      ${items.map(renderField).join("")}
    </div>`;
}

function renderField(e) {
  const tags = [];
  if (e.required) tags.push(`<span class="tag required">必填</span>`);
  if (e.secret) tags.push(`<span class="tag secret">敏感</span>`);
  tags.push(`<span class="tag source-${escapeAttr(e.active_source)}">当前来源：${activeSourceLabel(e.active_source)}</span>`);
  const inputHtml = renderInput(e);
  const defaultLine = e.default !== null && e.default !== "" && !e.secret ? `<div class="muted" style="font-size:12px;margin-top:6px">默认值：<code>${escapeHtml(JSON.stringify(e.default))}</code></div>` : '';
  return `<div class="field" data-field="${escapeAttr(e.field_name)}">
    <div class="field-head"><strong>${escapeHtml(e.label)}</strong><code>${escapeHtml(e.field_name)}</code>${tags.join("")}</div>
    <div class="field-desc">${escapeHtml(e.description)}</div>
    <div class="field-input">${inputHtml}</div>
    ${defaultLine}
  </div>`;
}

function renderInput(e) {
  const cur = e.current;
  if (e.kind === "toggle") {
    const on = cur === true || cur === "true" || cur === 1;
    return `<div class="toggle">
      <button class="${on?'on':''}" onclick="saveField('${escapeAttr(e.field_name)}', true)">开</button>
      <button class="${!on?'on':''}" onclick="saveField('${escapeAttr(e.field_name)}', false)">关</button>
    </div>`;
  }
  if (e.kind === "select") {
    const opts = e.choices.map(c => `<option value="${escapeAttr(c)}" ${cur===c?'selected':''}>${escapeHtml(c)}</option>`).join("");
    return `<select onchange="saveField('${escapeAttr(e.field_name)}', this.value)">${opts}</select>`;
  }
  if (e.kind === "json") {
    const text = cur == null ? "" : (typeof cur === "string" ? cur : JSON.stringify(cur, null, 2));
    return `<textarea data-raw="json" oninput="markDirty(this)">${escapeHtml(text)}</textarea>
      <button class="btn small primary" onclick="commitTextField('${escapeAttr(e.field_name)}', this, 'json')">保存</button>`;
  }
  if (e.kind === "int") {
    return `<input type="number" step="1" value="${escapeAttr(cur==null?'':cur)}" oninput="markDirty(this)">
      <button class="btn small primary" onclick="commitTextField('${escapeAttr(e.field_name)}', this, 'int')">保存</button>`;
  }
  if (e.kind === "float") {
    return `<input type="number" step="0.01" value="${escapeAttr(cur==null?'':cur)}" oninput="markDirty(this)">
      <button class="btn small primary" onclick="commitTextField('${escapeAttr(e.field_name)}', this, 'float')">保存</button>`;
  }
  if (e.kind === "secret") {
    return `<input type="password" placeholder="${cur ? '已设置（输入新值覆盖）' : '未设置'}" oninput="markDirty(this)">
      <button class="btn small primary" onclick="commitTextField('${escapeAttr(e.field_name)}', this, 'secret')">保存</button>`;
  }
  return `<input type="text" value="${escapeAttr(cur==null?'':cur)}" oninput="markDirty(this)">
    <button class="btn small primary" onclick="commitTextField('${escapeAttr(e.field_name)}', this, 'text')">保存</button>`;
}

function activeSourceLabel(src) {
  return ({env_file:".env.prod",env_json:"env.json",runtime_config:"runtime_config.json",default:"默认"})[src] || src;
}

function markDirty(el) { el.dataset.dirty = "1"; }

async function commitTextField(field, btn, kind) {
  const wrap = btn.parentElement;
  const input = wrap.querySelector("input, textarea");
  if (!input) return;
  let raw = input.value;
  let value = raw;
  if (kind === "int") value = parseInt(raw, 10);
  else if (kind === "float") value = parseFloat(raw);
  await saveField(field, value);
}

async function saveField(field, value) {
  try {
    const result = await api("/config/value", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({ field_name: field, value }) });
    if (result.success) { alertFlash("ok", `已保存 ${field}（已同步 .env 与 env.json）`); await loadView(); render(); }
    else { alertFlash("err", `保存部分失败：${(result.errors||[]).join("；")}`); await loadView(); render(); }
  } catch (e) { alertFlash("err", "保存失败：" + e.message); }
}

function pickGroup(g) { state.activeGroup = g; render(); }

function renderDevices() {
  const rows = state.devices.map(d => {
    const isCurrent = d.id === state.currentDeviceId;
    return `<tr>
      <td>${escapeHtml(d.label)} ${isCurrent ? '<span class="tag">当前</span>' : ''}</td>
      <td class="muted">${escapeHtml(d.ua.slice(0, 60))}</td>
      <td>${new Date(d.last_seen * 1000).toLocaleString()}</td>
      <td>${isCurrent ? '' : `<button class="btn small danger" onclick="revokeDevice('${escapeAttr(d.id)}')">撤销</button>`}</td>
    </tr>`;
  }).join("");
  return `<div class="card">
    <h2>已登录设备</h2>
    <table><thead><tr><th>设备</th><th>UA</th><th>最后活跃</th><th></th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

async function revokeDevice(id) {
  if (!confirm("撤销该设备？该设备下次访问将被踢出。")) return;
  try { await api("/auth/devices/" + encodeURIComponent(id), { method:"DELETE" }); alertFlash("ok", "已撤销"); await loadView(); render(); }
  catch (e) { alertFlash("err", "撤销失败：" + e.message); }
}

async function doLogout() {
  try { await api("/auth/logout", { method:"POST" }); } catch {}
  state.logged = false; render();
}

function attachLayout() {
  document.querySelectorAll("aside nav a").forEach(a => {
    a.addEventListener("click", async (ev) => {
      ev.preventDefault();
      state.view = a.getAttribute("href").slice(1);
      try { await loadView(); render(); } catch (e) { alertFlash("err", e.message); }
    });
  });
}

function renderLogin() {
  return `<div class="login-wrap"><div class="card"><h2>拟人插件 WebUI 登录</h2>
    <div id="login-step1">
      <label>管理员 QQ</label>
      <input id="login-qq" type="text" placeholder="例如 10001">
      <div style="margin-top:14px"><button class="btn primary" onclick="sendCode()">发送验证码</button></div>
      <p class="muted" style="margin-top:14px;font-size:12.5px">点击发送后，Bot 会向该 QQ 私聊推送 6 位数验证码，5 分钟内有效。</p>
    </div>
    <div id="login-step2" style="display:none">
      <label>验证码（来自 Bot 私聊）</label>
      <input id="login-code" type="text" inputmode="numeric" maxlength="6" placeholder="6 位数字">
      <label style="margin-top:10px">设备名称（便于识别）</label>
      <input id="login-label" type="text" placeholder="例如 公司笔记本">
      <div style="margin-top:14px"><button class="btn primary" onclick="doVerify()">验证并登录</button></div>
    </div>
    <div id="login-msg" class="muted" style="margin-top:14px"></div>
  </div></div>`;
}

function attachLogin() { /* 节点内 onclick 已绑定 */ }

async function sendCode() {
  const qq = document.getElementById("login-qq").value.trim();
  const msg = document.getElementById("login-msg");
  msg.textContent = "正在发送…";
  try {
    await api("/auth/login", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({ qq }) });
    state.pendingQq = qq;
    document.getElementById("login-step1").style.display = "none";
    document.getElementById("login-step2").style.display = "block";
    msg.textContent = "已发送，请到 QQ 私聊查看 6 位数验证码。";
  } catch (e) { msg.textContent = "发送失败：" + e.message; }
}

async function doVerify() {
  const code = document.getElementById("login-code").value.trim();
  const label = document.getElementById("login-label").value.trim();
  const msg = document.getElementById("login-msg");
  msg.textContent = "正在验证…";
  try {
    await api("/auth/verify", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({ qq: state.pendingQq, code, device_label: label }) });
    state.logged = true; state.qq = state.pendingQq; await loadView(); render();
  } catch (e) { msg.textContent = "验证失败：" + e.message; }
}

function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function escapeAttr(s) { return escapeHtml(s).replace(/'/g, "&#39;"); }

window.addEventListener("hashchange", async () => {
  const v = location.hash.slice(1);
  if (v) { state.view = v; try { await loadView(); } catch {} render(); }
});

if (location.hash) state.view = location.hash.slice(1);
bootstrap();
</script>
</body>
</html>
"""
