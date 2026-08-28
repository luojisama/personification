function favorabilityScoreText(fav) {
  if (!fav || fav.available === false) return "不可用";
  if (fav.enabled === false) return fav.exists === false ? "已关闭 · 默认未建档" : "已关闭";
  const score = Number(fav.score || 0);
  return `${score.toFixed(2)}${fav.level ? " · " + fav.level : ""}${fav.exists === false ? " · 默认" : ""}`;
}

function renderFavorabilityBadge(fav) {
  if (!fav || fav.available === false) return '<span class="muted">—</span>';
  const score = Number(fav.score || 0);
  let style = 'background:rgba(106,168,255,0.18);color:var(--accent)';
  if (fav.enabled === false) style = 'background:var(--tag-bg);color:var(--muted)';
  else if (score >= 85) style = 'background:rgba(52,211,153,0.18);color:var(--ok)';
  else if (score < 0) style = 'background:rgba(248,113,113,0.16);color:var(--danger)';
  else if (score < 20) style = 'background:rgba(245,158,11,0.16);color:var(--warn)';
  if (fav.is_perm_blacklisted) style = 'background:rgba(248,113,113,0.16);color:var(--danger)';
  const text = favorabilityScoreText(fav);
  const stateHint = fav.exists === false ? "虚拟默认值，尚未创建档案" : (fav.enabled === false ? "功能已关闭" : "已持久化档案");
  return `<span class="tag favorability-badge u-tabular" style="${style}" title="好感度 ${escapeAttr(text)}；${escapeAttr(stateHint)}">${escapeHtml(text)}</span>`;
}

function renderFavorabilityCard(fav, title) {
  if (!fav || fav.available === false) {
    return `<div class="card"><h2>${escapeHtml(title)}</h2><p class="muted">好感度服务未就绪。</p></div>`;
  }
  const events = fav.events || [];
  const eventRows = events.map(e => {
    const delta = Number(e.delta || 0);
    const deltaText = (delta > 0 ? "+" : "") + delta.toFixed(2);
    const color = delta > 0 ? "var(--ok)" : (delta < 0 ? "var(--danger)" : "var(--muted)");
    const when = e.timestamp ? new Date(e.timestamp*1000).toLocaleString() : (e.date || "-");
    const sourceMode = [e.source_label || e.source || "", e.mode === "shadow" ? "拟议" : ""].filter(Boolean).join(" · ");
    const tags = Array.isArray(e.behavior_tags) ? e.behavior_tags.join("、") : "";
    const detail = [e.reason || "", sourceMode, tags ? `标签：${tags}` : "", e.confidence ? `置信度 ${(Number(e.confidence) * 100).toFixed(0)}%` : "", e.evidence_summary ? `证据：${e.evidence_summary}` : ""].filter(Boolean).join(" · ");
    return `<tr>
      <td class="col-time u-atomic u-tabular">${escapeHtml(when)}</td>
      <td class="col-summary u-wrap">${escapeHtml(e.label || "其他好感事件")}</td>
      <td class="col-number u-atomic u-tabular" style="color:${color};font-weight:600">${escapeHtml(deltaText)}</td>
      <td class="col-status"><span class="tag tag--status">${escapeHtml(e.status_label || "")}</span></td>
      <td class="col-description u-wrap">${escapeHtml(detail)}</td>
    </tr>`;
  }).join("");
  const last = fav.latest_event;
  const lastLine = last
    ? `${last.label || "其他好感事件"} ${(Number(last.delta || 0) > 0 ? "+" : "")}${Number(last.delta || 0).toFixed(2)}`
    : "暂无事件";
  const policy = fav.behavior_policy || {};
  const policyLine = policy.band ? `行为带：${policy.band} · 温度：${policy.warmth || "neutral"} · 回复长度：${policy.reply_length || "normal"}` : "行为策略未就绪";
  const signed = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${Number(value || 0).toFixed(2)}`;
  const bandRows = Object.entries(fav.behavior_bands || {}).map(([band, item]) => {
    const value = item && typeof item === "object" ? item : {};
  return `<tr><td>${escapeHtml(band)}</td><td>${escapeHtml(value.warmth || "")}</td><td>${escapeHtml(value.reply_length || "")}</td><td class="u-tabular">${signed(value.random_reply_add)}</td><td class="u-tabular">${signed(value.group_idle_add)}</td></tr>`;
  }).join("");
  const changeLine = last
    ? `${last.mode === "shadow" ? "拟议变化" : "最近变化"}：<span style="color:${Number(last.delta || 0) >= 0 ? "var(--ok)" : "var(--danger)"};font-weight:700">${escapeHtml((Number(last.delta || 0) > 0 ? "+" : "") + Number(last.delta || 0).toFixed(2))}</span> · ${escapeHtml(last.source_label || last.source || "旧事件")} · ${escapeHtml(last.reason || "无原因说明")}`
    : "暂无好感度变化";
  return `<div class="card">
    <div class="between" style="gap:12px;flex-wrap:wrap">
      <h2 style="margin:0">${escapeHtml(title)}</h2>
      ${renderFavorabilityBadge(fav)}
    </div>
    ${fav.exists === false ? `<p class="muted">${fav.enabled === false ? '好感度功能当前已关闭；' : ''}下方展示配置中的虚拟默认值，尚未持久化；浏览此页面不会创建好感度档案。</p>` : (fav.enabled === false ? '<p class="muted">好感度功能当前已关闭，不会记录新的关系事件。</p>' : '')}
    <div class="row" style="gap:24px;margin-top:12px">
      <div><div class="muted">${fav.exists === false ? '默认分值（未建档）' : '当前分值'}（${Number(fav.score_min ?? -100).toFixed(0)}..${Number(fav.score_max ?? 100).toFixed(0)}）</div><div class="u-atomic u-tabular" style="font-size:22px;font-weight:700;color:${Number(fav.score || 0) < 0 ? 'var(--danger)' : 'inherit'}">${Number(fav.score || 0).toFixed(2)}</div></div>
      <div><div class="muted">等级</div><div class="u-atomic" style="font-size:18px;color:${Number(fav.score || 0) < 0 ? '#d65a5a' : 'inherit'}">${escapeHtml(fav.level || "—")}</div></div>
      <div><div class="muted">今日净变化</div><div class="u-atomic u-tabular">${signed(fav.daily_net_count)}</div></div>
      <div><div class="muted">今日加分</div><div class="u-atomic u-tabular">${Number(fav.daily_positive_count || 0).toFixed(2)}</div></div>
      <div><div class="muted">今日扣分</div><div class="u-atomic u-tabular">${Number(fav.daily_negative_count || 0).toFixed(2)}</div></div>
      <div><div class="muted">最近事件</div><div>${escapeHtml(lastLine)}</div></div>
      ${fav.is_perm_blacklisted ? '<div><div class="muted">黑名单</div><div style="color:var(--danger)">永久黑名单</div></div>' : ''}
    </div>
    <p class="muted" style="margin:10px 0 0">${escapeHtml(policyLine)}</p>
    <p style="margin:6px 0 0">${changeLine}</p>
    ${fav.observer ? `<p class="muted" style="margin:6px 0 0">观察器：${escapeHtml(fav.observer.mode || "shadow")} · 排队 ${Number(fav.observer.queued || 0)} · 拟议 ${Number(fav.observer.projected || 0)} · 已应用 ${Number(fav.observer.applied || 0)} · 跳过 ${Number(fav.observer.skipped || 0)}</p>` : ''}
    ${bandRows ? `<details style="margin-top:8px"><summary class="muted" style="cursor:pointer">正负关系行为策略</summary><div class="table-wrap table-scroll" tabindex="0" role="region" aria-label="正负关系行为策略"><table class="data-table" style="margin-top:8px"><thead><tr><th scope="col">行为带</th><th scope="col">温度</th><th scope="col">长度</th><th scope="col">群随机偏置</th><th scope="col">群闲偏置</th></tr></thead><tbody>${bandRows}</tbody></table></div></details>` : ''}
    ${events.length ? `<details style="margin-top:12px"><summary class="muted" style="cursor:pointer">最近好感事件</summary>
      <div class="table-wrap table-scroll" tabindex="0" role="region" aria-label="最近好感事件"><table class="data-table wide" style="margin-top:8px"><thead><tr><th scope="col" class="col-time">时间</th><th scope="col" class="col-summary">事件</th><th scope="col" class="col-number">变化</th><th scope="col" class="col-status">状态</th><th scope="col" class="col-description">原因</th></tr></thead><tbody>${eventRows}</tbody></table></div>
    </details>` : ''}
  </div>`;
}
const ADMIN_OPERATION_STORAGE_KEY = "personification_admin_operation_diagnostics_v1";

function adminOperationEntries() {
  if (Array.isArray(state.adminOperationDiagnostics)) return state.adminOperationDiagnostics;
  try {
    const saved=JSON.parse(sessionStorage.getItem(ADMIN_OPERATION_STORAGE_KEY)||"[]");
    state.adminOperationDiagnostics=Array.isArray(saved)?saved.slice(0,16):[];
  } catch { state.adminOperationDiagnostics=[]; }
  return state.adminOperationDiagnostics;
}

function rememberAdminOperation(scope, value, fallbackTitle="管理操作未完成") {
  const diagnostic=value&&value.diagnostic&&typeof value.diagnostic==="object"
    ? value.diagnostic
    : (value instanceof Error ? operationDiagnosticFromError(value,fallbackTitle) : value);
  if(!diagnostic||typeof diagnostic!=="object"||!diagnostic.code)return null;
  state.adminOperationDiagnostics=[{scope,diagnostic},...adminOperationEntries()].slice(0,16);
  try{sessionStorage.setItem(ADMIN_OPERATION_STORAGE_KEY,JSON.stringify(state.adminOperationDiagnostics));}catch{}
  return diagnostic;
}

function clearAdminOperations(scope) {
  state.adminOperationDiagnostics=adminOperationEntries().filter(item=>item.scope!==scope);
  try{sessionStorage.setItem(ADMIN_OPERATION_STORAGE_KEY,JSON.stringify(state.adminOperationDiagnostics));}catch{}
  render();
}

function renderAdminOperations(scope,title) {
  const items=renderOperationHistory(adminOperationEntries().filter(item=>item.scope===scope).map(item=>item.diagnostic),{group:`view-${state.view}`});
  return items?`<div class="card"><div class="between"><h2>${escapeHtml(title)}</h2><button class="btn small" onclick="clearAdminOperations('${escapeAttr(scope)}')">清空</button></div>${items}</div>`:"";
}
