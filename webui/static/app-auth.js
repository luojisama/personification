const SMALL_OPERATION_STORAGE_KEY = "personification_small_operation_diagnostics_v1";

function smallOperationEntries() {
  if (Array.isArray(state.smallOperationDiagnostics)) return state.smallOperationDiagnostics;
  try {
    const saved = JSON.parse(sessionStorage.getItem(SMALL_OPERATION_STORAGE_KEY) || "[]");
    state.smallOperationDiagnostics = Array.isArray(saved) ? saved.slice(0, 12) : [];
  } catch { state.smallOperationDiagnostics = []; }
  return state.smallOperationDiagnostics;
}

function rememberSmallOperation(scope, value, fallbackTitle="操作未完成") {
  const diagnostic = value && value.diagnostic && typeof value.diagnostic === "object"
    ? value.diagnostic
    : (value instanceof Error ? operationDiagnosticFromError(value, fallbackTitle) : value);
  if (!diagnostic || typeof diagnostic !== "object" || !diagnostic.code) return null;
  state.smallOperationDiagnostics = [{scope, diagnostic}, ...smallOperationEntries()].slice(0, 12);
  try { sessionStorage.setItem(SMALL_OPERATION_STORAGE_KEY, JSON.stringify(state.smallOperationDiagnostics)); } catch {}
  return diagnostic;
}

function clearSmallOperations(scope) {
  state.smallOperationDiagnostics = smallOperationEntries().filter(item => item.scope !== scope);
  try { sessionStorage.setItem(SMALL_OPERATION_STORAGE_KEY, JSON.stringify(state.smallOperationDiagnostics)); } catch {}
  render();
}

function renderSmallOperations(scope, title) {
  const items = renderOperationHistory(
    smallOperationEntries().filter(item => item.scope === scope).map(item => item.diagnostic),
    {group:`view-${state.view}`},
  );
  return items ? `<div class="card"><div class="between"><h2>${escapeHtml(title)}</h2><button class="btn small" onclick="clearSmallOperations('${escapeAttr(scope)}')">清空</button></div>${items}</div>` : "";
}

function renderDevices() {
  const rows = state.devices.map(d => {
    const isCurrent = d.id === state.currentDeviceId;
    return `<tr>
      <td class="col-model"><span class="u-clamp-2" title="${escapeAttr(d.label)} · device ${escapeAttr(d.id)}">${escapeHtml(d.label)}</span> ${isCurrent ? '<span class="tag tag--status">当前</span>' : ''}</td>
      <td class="col-description muted u-wrap" title="${escapeAttr(d.ua)}">${escapeHtml(d.ua.slice(0, 60))}</td>
      <td class="col-time u-atomic u-tabular">${new Date(d.last_seen * 1000).toLocaleString()}</td>
      <td class="col-actions">
        ${isCurrent ? '' : `<button class="btn small danger" aria-label="撤销设备 ${escapeAttr(d.label)}" onclick="revokeDevice('${escapeAttr(d.id)}')">撤销</button>`}
      </td>
    </tr>`;
  }).join("");
  return `${renderSmallOperations("device", "设备操作诊断")}<div class="card">
    <h2>已登录设备</h2>
    <button class="btn" onclick="setCurrentDeviceTrust()" ${state.authIdentity?.trusted || canTrustThisBrowser() ? "" : "disabled"}>${state.authIdentity?.trusted ? "取消当前设备信任" : "信任当前设备"}</button>
    <p class="muted">受信任浏览器可自动登录，需通过 HTTPS 或本机地址访问。退出或撤销后需要重新验证。</p>
    <div class="table-wrap table-scroll" tabindex="0" role="region" aria-label="已登录设备列表"><table class="data-table wide"><thead><tr><th scope="col" class="col-model">设备</th><th scope="col" class="col-description">UA</th><th scope="col" class="col-time">最后活跃</th><th scope="col" class="col-actions"><span class="sr-only">操作</span></th></tr></thead><tbody>${rows}</tbody></table></div>
  </div>`;
}

async function setCurrentDeviceTrust() {
  const me = state.authIdentity || await api("/auth/me");
  try {
    if (me.trusted && me.trust_id) {
      await api("/auth/trusted-devices/" + encodeURIComponent(me.trust_id), {method:"DELETE"});
      _automaticTrustedRecovery = false; _authGeneration += 1; state.logged = false; clearInMemorySensitiveState(); await refreshEligibleAdmins(); render();
    } else {
      await api("/auth/devices/" + encodeURIComponent(me.device_id) + "/trust", {method:"POST"});
      state.authIdentity = await api("/auth/me"); await loadView(); render();
    }
  } catch (error) { alertFlash("err", "信任设置未完成，请稍后重试。"); }
}

async function approveDevice(id) {
  try {
    const result = await api("/auth/devices/" + encodeURIComponent(id) + "/approve", { method:"POST" });
    const diagnostic = rememberSmallOperation("device", result, "设备审批未完成");
    alertFlash(diagnostic?.ok === false || diagnostic?.partial ? "info" : "ok", diagnostic?.title || "已批准");
    await loadView(); render();
  } catch (e) { const diagnostic = rememberSmallOperation("device", e, "设备审批未完成"); alertFlash("err", diagnostic?.title || "设备审批未完成"); render(); }
}

async function revokeDevice(id) {
  if (!confirm("撤销该设备？该设备下次访问将被踢出。")) return;
  try {
    const result = await api("/auth/devices/" + encodeURIComponent(id), { method:"DELETE" });
    const diagnostic = rememberSmallOperation("device", result, "设备撤销未完成");
    alertFlash(diagnostic?.ok === false || diagnostic?.partial ? "info" : "ok", diagnostic?.title || "已撤销");
    await loadView(); render();
  }
  catch (e) { const diagnostic = rememberSmallOperation("device", e, "设备撤销未完成"); alertFlash("err", diagnostic?.title || "设备撤销未完成"); render(); }
}

async function doLogout() {
  _automaticTrustedRecovery = false; _authGeneration += 1; state.authChecking = false; state.authUnavailable = false;
  const generation = _authGeneration;
  leaveViewLifecycle(state.view, ""); clearInMemorySensitiveState(); state.logged = false; state.devicePending = false;
  state.logoutUnconfirmed = false; render();
  try {
    await api("/auth/logout", { method:"POST", headers:{"X-Personification-Refresh":"1"} });
    if (generation !== _authGeneration) return;
  } catch (error) {
    if (generation !== _authGeneration) return;
    state.logoutUnconfirmed = true;
  }
  await refreshEligibleAdmins();
  if (generation === _authGeneration) render();
}

let _layoutDelegationAttached = false;

function attachLayout() {
  if (!_layoutDelegationAttached) {
    _layoutDelegationAttached = true;
    document.addEventListener("click", event => {
      const target = event.target instanceof Element ? event.target : null;
      const nav = target?.closest("#console-sidebar nav a[href^='#']");
      if (nav) {
        event.preventDefault();
        navigateToView(nav.getAttribute("href").slice(1));
        return;
      }
      const leave = target?.closest(".qq-leave-group");
      if (leave && typeof qqLeaveGroup === "function") {
        qqLeaveGroup(leave.dataset.groupId, leave.dataset.groupName);
      }
    });
  }
  const main=document.querySelector(".layout > main");
  if(main&&!main.dataset.scrollListenerBound){
    main.dataset.scrollListenerBound="true";
    main.addEventListener("scroll",queueScrollStateCapture,{passive:true});
  }
  const nav=document.querySelector("#console-sidebar nav");
  if(nav&&!nav.dataset.scrollListenerBound){
    nav.dataset.scrollListenerBound="true";
    nav.addEventListener("scroll",queueScrollStateCapture,{passive:true});
  }
}

function canTrustThisBrowser() { return location.protocol === "https:" || ["localhost", "127.0.0.1", "::1", "[::1]"].includes(location.hostname); }

function renderLogin() {
  if (state.authChecking) return `<div class="login-wrap"><div class="card" role="status">正在恢复受信任设备的登录状态…</div></div>`;
  if (state.authUnavailable) return `<div class="login-wrap"><div class="card" role="alert"><h2>暂时无法连接</h2><p>请检查网络后重试连接。</p><button class="btn primary" onclick="state.authUnavailable=false;bootstrap()">重试连接</button></div></div>`;
  const themeIcon = state.theme === "dark" ? renderIcon("sun") : renderIcon("moon");
  const themeLabel = state.theme === "dark" ? "切换到浅色主题" : "切换到深色主题";
  const eligible = state.eligibleAdmins || [];
  let picker, hint;
  if (!eligible.length) {
    picker = `<select id="login-qq" disabled style="width:100%;margin-top:6px"><option value="">未配置管理员</option></select>`;
    hint = `<p class="muted" style="margin-top:14px;font-size:12.5px">请先在 NoneBot 配置中设置 SUPERUSERS，或通过已有管理入口添加 plugin admin。</p>`;
  } else {
    picker = `<select id="login-qq" style="width:100%;margin-top:6px">
        ${eligible.map(e => `<option value="${escapeAttr(e.qq)}">${escapeHtml(e.qq)}（${escapeHtml(e.source)}）</option>`).join("")}
      </select>`;
    hint = `<p class="muted" style="margin-top:14px;font-size:12.5px">选择一个管理员 QQ，Bot 会向其私聊推送 6 位数验证码，5 分钟内有效。</p>`;
  }
  return `<div class="login-wrap"><div class="card"><div class="between">
      <h2 style="margin:0">拟人插件 WebUI 登录</h2>
       <button class="btn small icon-btn" onclick="toggleTheme()" title="${themeLabel}" aria-label="${themeLabel}">${themeIcon}</button>
    </div>
    <div id="login-step1">
      <label>管理员 QQ</label>
      ${picker}
      <div style="margin-top:14px"><button class="btn primary" onclick="sendCode()" ${eligible.length ? "" : "disabled"}>发送验证码</button></div>
      ${hint}
    </div>
    <div id="login-step2" style="display:none">
      <div class="alert info" style="font-size:12.5px">验证码已发送到所选管理员 QQ，请在 5 分钟内输入。</div>
      <label>验证码（来自 Bot 私聊）</label>
      <input id="login-code" type="text" inputmode="numeric" maxlength="6" placeholder="6 位数字">
      <label style="margin-top:10px">设备名称（便于识别）</label>
      <input id="login-label" type="text" placeholder="例如 公司笔记本">
      <label style="display:flex;align-items:center;gap:8px;margin-top:14px"><input id="login-trust-device" type="checkbox" ${canTrustThisBrowser() ? "checked" : "disabled"} style="width:auto">信任此设备</label>
      <p class="muted">个人设备可保持勾选，以后自动登录；共享设备请取消。信任仅保存在当前浏览器，需通过 HTTPS 或本机地址访问。</p>
      <div style="margin-top:14px"><button class="btn primary" onclick="doVerify()">验证并登录</button></div>
    </div>
    <div id="login-msg" class="muted" style="margin-top:14px"></div>
    ${state.logoutUnconfirmed ? '<p class="alert" role="alert">当前页面已锁定，但服务器未确认注销。请重试注销；关闭并重新打开页面可能仍恢复登录。</p><button class="btn danger" onclick="doLogout()">重试注销</button>' : ''}
  </div></div>`;
}

function attachLogin() { /* 节点内 onclick 已绑定 */ }

function renderDevicePending() {
  return `<div class="login-wrap"><div class="card">
    <h2>设备等待审批</h2>
    <p class="muted">该设备已登记，但需由一台<strong>已批准的设备</strong>在「设备」页确认后才能使用。</p>
    <p class="muted">已通知管理员。批准后点击下方按钮刷新。</p>
    <div style="margin-top:14px;display:flex;gap:8px">
      <button class="btn primary" onclick="recheckDevice()">我已被批准，刷新</button>
      <button class="btn" onclick="logoutPending()">退出</button>
    </div>
  </div></div>`;
}

async function recheckDevice() {
  try { const me = await api("/auth/me"); state.logged = true; state.devicePending = false; state.qq = me.qq; await loadView(); render(); enterViewLifecycle(state.view); }
  catch (e) {
    if (/DEVICE_PENDING/.test(String(e && e.message || ""))) { alertFlash("info", "仍在等待管理员批准"); }
    else { state.devicePending = false; render(); }
  }
}

async function logoutPending() {
  await doLogout();
}

async function sendCode() {
  const el = document.getElementById("login-qq");
  const qq = (el && el.value || "").trim();
  const msg = document.getElementById("login-msg");
  if (!qq) { msg.textContent = "请选择管理员 QQ。"; return; }
  msg.textContent = "正在发送…";
  try {
    await api("/auth/login", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({ qq }) });
    state.pendingQq = qq;
    document.getElementById("login-step1").style.display = "none";
    document.getElementById("login-step2").style.display = "block";
    msg.textContent = "验证码已发送，请查收管理员 QQ 私聊。";
  } catch (e) { msg.textContent = "发送失败：" + e.message; }
}

async function doVerify() {
  const generation = ++_authGeneration;
  const code = document.getElementById("login-code").value.trim();
  const label = document.getElementById("login-label").value.trim();
  const msg = document.getElementById("login-msg");
  msg.textContent = "正在验证…";
  let r;
  try {
    r = await api("/auth/verify", { method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify({ qq: state.pendingQq, code, device_label: label, trust_device: document.getElementById("login-trust-device").checked }) });
  } catch (e) { msg.textContent = "验证失败：" + e.message; }
  if (!r || generation !== _authGeneration || r.success !== true) return;
  _automaticTrustedRecovery = true; state.authUnavailable = false; state.logoutUnconfirmed = false;
  clearInMemorySensitiveState();
  if (r.pending) { state.logged = false; state.devicePending = true; state.qq = state.pendingQq; render(); return; }
  state.logged = true; state.devicePending = false; state.qq = state.pendingQq;
  try {
    state.authIdentity = await api("/auth/me");
    if (generation !== _authGeneration) return;
    const loaded = await loadView();
    if (loaded) { render(); enterViewLifecycle(state.view); }
  } catch (e) {
    const failedView = state.view;
    state.view = "dashboard";
    history.replaceState({view:state.view}, "", "#dashboard");
    const loaded = await loadView().catch(() => false);
    if (loaded) {
      state.alert = {kind:"err", text:`登录成功，但“${failedView}”页面加载失败：${e.message}`};
      render();
      enterViewLifecycle(state.view);
    }
  }
}

function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function escapeAttr(s) { return escapeHtml(s).replace(/'/g, "&#39;"); }

async function navigateFromBrowserHistory() {
  const view = normalizeView(location.hash.slice(1));
  if (view !== state.view) await navigateToView(view, {fromHistory:true});
}

window.addEventListener("popstate", navigateFromBrowserHistory);
window.addEventListener("hashchange", navigateFromBrowserHistory);
window.addEventListener("beforeunload", captureScrollState);

state.view = normalizeView(location.hash.slice(1));
history.replaceState({view:state.view}, "", `#${state.view}`);
bootstrap();
