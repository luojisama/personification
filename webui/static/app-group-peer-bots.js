/* Peer Bot registration, templates, discovery and loop diagnostics for legacy group management. */

function emptyLegacyPeerCommandDraft(targetBotId) {
  return {
    target_bot_id: String(targetBotId || ""),
    command_id: "",
    full_template: "",
    parameter_schema_text: JSON.stringify({type:"object",properties:{},required:[],additionalProperties:false}, null, 2),
    risk_level: "read",
    status: "candidate",
    arguments_text: "{}",
  };
}

function legacyPeerValueMatchesType(value,type) {
  if (type==="string") return typeof value==="string";
  if (type==="integer") return typeof value==="number"&&Number.isInteger(value);
  if (type==="number") return typeof value==="number"&&Number.isFinite(value);
  if (type==="boolean") return typeof value==="boolean";
  return false;
}

function validateLegacyPeerCommand(draft, maxCommandChars) {
  if (!draft || !/^[A-Za-z0-9_.:-]{1,80}$/.test(String(draft.target_bot_id || ""))) return {error:"请选择有效的目标 Bot", fields:[], schema:null};
  if (!/^[A-Za-z0-9_.:-]{1,80}$/.test(String(draft.command_id || ""))) return {error:"命令 ID 格式无效", fields:[], schema:null};
  const template = String(draft.full_template || "").trim();
  if (!template || /[\r\n\x00-\x1f\x7f-\x9f]/.test(template)) return {error:"完整命令模板必须是无控制字符的单行文本", fields:[], schema:null};
  const maxChars=Math.max(1,Math.min(4000,Number(maxCommandChars||state.groupPeerBots?.max_command_chars||500)));
  if (template.length>maxChars) return {error:`完整命令模板不能超过 ${maxChars} 个字符`,fields:[],schema:null};
  const fields = [];
  let literalHead="";let beforeFirstField=true;
  for (let i=0;i<template.length;) {
    if (template[i]==="{" && template[i+1]==="{") { if(beforeFirstField) literalHead+="{";i+=2;continue; }
    if (template[i]==="}" && template[i+1]==="}") { if(beforeFirstField) literalHead+="}";i+=2;continue; }
    if (template[i]==="}") return {error:"模板包含未配对的右花括号",fields:[],schema:null};
    if (template[i]!=="{") { if(beforeFirstField) literalHead+=template[i];i+=1;continue; }
    const end=template.indexOf("}",i+1);
    if (end<0) return {error:"模板占位符未闭合",fields:[],schema:null};
    const field=template.slice(i+1,end);
    if (!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(field) || fields.includes(field)) return {error:"模板参数名无效或重复",fields:[],schema:null};
    beforeFirstField=false;fields.push(field);i=end+1;
  }
  if (!literalHead.replace(/\s+/g," ").trim()) return {error:"模板必须以固定命令前缀开头，不能以参数占位符开头",fields:[],schema:null};
  try {
    const schema=JSON.parse(String(draft.parameter_schema_text || ""));
    if (!schema || schema.type!=="object" || schema.additionalProperties!==false || !schema.properties || !Array.isArray(schema.required)) throw new Error("schema 必须声明 object/properties/required/additionalProperties=false");
    const names=Object.keys(schema.properties).sort();
    if (names.join("\0")!==[...fields].sort().join("\0")) throw new Error("properties 必须与模板占位符完全一致");
    if (schema.required.some(name=>!names.includes(name))) throw new Error("required 包含未声明参数");
    for (const [name,definition] of Object.entries(schema.properties)) {
      if (!definition||typeof definition!=="object"||!["string","integer","number","boolean"].includes(String(definition.type))) throw new Error(name+" 使用了不支持的参数类型");
      if (definition.enum!==undefined&&(!Array.isArray(definition.enum)||definition.enum.length<1||definition.enum.length>30||definition.enum.some(value=>!legacyPeerValueMatchesType(value,definition.type)))) throw new Error(name+" 的 enum 与参数类型不一致");
    }
    return {error:"",fields,schema};
  } catch(e) { return {error:"参数 schema 无效："+(e.message||"无法解析"),fields,schema:null}; }
}

function renderLegacyPeerDryRun(draft, validation) {
  if (validation.error || !validation.schema) return validation.error || "模板无效";
  let args;
  try { args=JSON.parse(String(draft.arguments_text||"{}")); }
  catch(e) { return "Dry-run 参数无效："+(e.message||"无法解析"); }
  if (!args || typeof args!=="object" || Array.isArray(args)) return "Dry-run 参数必须是 JSON 对象";
  if (Object.keys(args).some(key=>!validation.fields.includes(key))) return "Dry-run 包含 schema 未声明参数";
  for (const name of validation.fields) if (!(name in args)) return "Dry-run 缺少模板参数："+name;
  for (const name of validation.fields) {
    const definition=validation.schema.properties[name];const value=args[name];
    if (!legacyPeerValueMatchesType(value,definition.type)) return `Dry-run 参数 ${name} 类型必须是 ${definition.type}`;
    if (definition.type==="string"&&definition.maxLength!==undefined&&String(value).length>Number(definition.maxLength)) return `Dry-run 参数 ${name} 不能超过 ${definition.maxLength} 个字符`;
    if ((definition.type==="integer"||definition.type==="number")&&definition.minimum!==undefined&&Number(value)<Number(definition.minimum)) return `Dry-run 参数 ${name} 不能小于 ${definition.minimum}`;
    if ((definition.type==="integer"||definition.type==="number")&&definition.maximum!==undefined&&Number(value)>Number(definition.maximum)) return `Dry-run 参数 ${name} 不能大于 ${definition.maximum}`;
    if (Array.isArray(definition.enum)&&!definition.enum.some(candidate=>Object.is(candidate,value))) return `Dry-run 参数 ${name} 不在允许的 enum 中`;
  }
  const template=String(draft.full_template||"").trim();let rendered="";
  for (let i=0;i<template.length;) {
    if (template[i]==="{" && template[i+1]==="{") { rendered+="{";i+=2;continue; }
    if (template[i]==="}" && template[i+1]==="}") { rendered+="}";i+=2;continue; }
    if (template[i]!=="{") { rendered+=template[i];i+=1;continue; }
    const end=template.indexOf("}",i+1);const name=template.slice(i+1,end);const value=name in args?String(args[name]):`{${name}}`;
    if (/[\r\n\x00-\x1f\x7f-\x9f]/.test(value)) return "Dry-run 参数包含控制字符："+name;
    rendered+=value;i=end+1;
  }
  return "本地校验通过，未发送任何 QQ 消息："+rendered;
}

async function refreshLegacyPeerBots() {
  const gid=state.selectedGroup;if (!gid) return;
  state.groupPeerBots=await api("/groups/"+encodeURIComponent(gid)+"/peer-bots");
}

async function mutateLegacyPeerBot(path, options, successText) {
  const gid=state.selectedGroup;if (!gid) return;
  try {
    const result=await api("/groups/"+encodeURIComponent(gid)+"/peer-bots"+path, options||{method:"POST",headers:{"content-type":"application/json"},body:"{}"});
    rememberAdminOperation("group",result,"Peer Bot 管理未完成");await refreshLegacyPeerBots();alertFlash("ok",result.title||successText);render();
  } catch(e) { const d=rememberAdminOperation("group",e,"Peer Bot 管理未完成");alertFlash("err",d?.title||"Peer Bot 管理未完成");render(); }
}

async function saveLegacyPeerSettings() {
  const enabled=!!document.getElementById("legacy-peer-enabled")?.checked;
  const cooldown=Number(document.getElementById("legacy-peer-cooldown")?.value);
  const ttl=Number(document.getElementById("legacy-peer-ttl")?.value);
  if (!Number.isFinite(cooldown)||cooldown<0||cooldown>3600||!Number.isFinite(ttl)||ttl<1||ttl>600) { alertFlash("err","冷却或 TTL 超出范围");return; }
  if (state.groupPeerBots?.enabled && !enabled && !confirm("确认停用本群 Peer Bot 调用？")) return;
  await mutateLegacyPeerBot("/settings",{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify({enabled,max_calls_per_turn:1,cooldown_seconds:cooldown,pending_ttl_seconds:ttl,max_chain_depth:1})},"Peer Bot 设置已保存");
}

async function setLegacyPeerBotStatus(uid, action) {
  if ((action==="reject"||action==="clear")&&!confirm(`确认对 ${uid} 执行 ${action}？`)) return;
  await mutateLegacyPeerBot("/"+encodeURIComponent(uid),{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify({action})},"Bot 状态已更新");
}

async function discoverLegacyPeerBots() {
  if (!confirm("只评估本群受限历史投影或已缓冲微批，不会自动授权。继续？")) return;
  await mutateLegacyPeerBot("/discover",{method:"POST",headers:{"content-type":"application/json"},body:"{}"},"候选发现已完成");
}

async function resetLegacyPeerLoop() {
  if (!confirm("确认清除本群进程内 pending 与 cooldown？不会自动重发。")) return;
  await mutateLegacyPeerBot("/reset-loop",{method:"POST",headers:{"content-type":"application/json"},body:"{}"},"循环保护已复位");
}

function editLegacyPeerCommand(command) {
  state.groupPeerCommandDraft={target_bot_id:command.target_bot_id,command_id:command.command_id,full_template:command.full_template,parameter_schema_text:JSON.stringify(command.parameter_schema||{},null,2),risk_level:command.risk_level,status:command.status,arguments_text:"{}"};state.groupPeerDryRun="";render();
}

function editLegacyPeerCommandById(commandId) {
  const command=(state.groupPeerBots?.commands||[]).find(item=>String(item.command_id)===String(commandId));
  if (command) editLegacyPeerCommand(command);
}

async function saveLegacyPeerCommand() {
  const draft=state.groupPeerCommandDraft;const validation=validateLegacyPeerCommand(draft);
  if (validation.error) { alertFlash("err",validation.error);return; }
  await mutateLegacyPeerBot("/"+encodeURIComponent(draft.target_bot_id)+"/commands/"+encodeURIComponent(draft.command_id),{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify({full_template:String(draft.full_template).trim(),parameter_schema:validation.schema,risk_level:draft.risk_level,status:draft.status})},"命令模板已保存");
}

async function deleteLegacyPeerCommand(uid, commandId) {
  if (!confirm(`确认删除命令 ${commandId}？`)) return;
  await mutateLegacyPeerBot("/"+encodeURIComponent(uid)+"/commands/"+encodeURIComponent(commandId),{method:"DELETE"},"命令模板已删除");
}

function renderGroupPeerBots() {
  const data=state.groupPeerBots;
  if (!data) return '<div class="card"><h2>Peer Bot 协作</h2><p class="muted">注册表当前不可用或尚未加载。</p></div>';
  const bots=Array.isArray(data.bots)?data.bots:[];const commands=Array.isArray(data.commands)?data.commands:[];const invocations=Array.isArray(data.recent_invocations)?data.recent_invocations:[];
  const botRows=bots.map(bot=>`<tr><td class="col-model">${escapeHtml(bot.nickname||"未命名")}</td><td class="col-id"><code>${escapeHtml(bot.user_id)}</code></td><td class="col-number">${Math.round(Number(bot.confidence||0)*100)}%</td><td class="col-status"><span class="tag">${escapeHtml(bot.source||"")}</span> <span class="tag tag--status">${escapeHtml(bot.status||"")}</span></td><td class="col-description u-wrap">${escapeHtml((bot.evidence_tags||[]).join(" / ")||"—")}</td><td class="col-actions"><button class="btn small primary" onclick="setLegacyPeerBotStatus('${escapeAttr(bot.user_id)}','approve')" ${bot.status==="approved"?'disabled':''}>批准</button> <button class="btn small" onclick="setLegacyPeerBotStatus('${escapeAttr(bot.user_id)}','reject')" ${bot.status==="rejected"?'disabled':''}>拒绝</button> <button class="btn small danger" onclick="setLegacyPeerBotStatus('${escapeAttr(bot.user_id)}','clear')" ${!bot.manual_override?'disabled':''}>清除覆盖</button></td></tr>`).join("");
  const commandRows=commands.map(command=>`<tr><td class="col-id"><code>${escapeHtml(command.command_id)}</code></td><td class="col-id"><code>${escapeHtml(command.target_bot_id)}</code></td><td class="col-description"><code class="u-wrap">${escapeHtml(command.full_template)}</code></td><td class="col-status"><span class="tag">${escapeHtml(command.risk_level)}</span> ${escapeHtml(command.status)}</td><td class="col-actions"><button class="btn small" onclick="editLegacyPeerCommandById('${escapeAttr(command.command_id)}')">编辑 / Dry-run</button> <button class="btn small danger" onclick="deleteLegacyPeerCommand('${escapeAttr(command.target_bot_id)}','${escapeAttr(command.command_id)}')">删除</button></td></tr>`).join("");
  const invocationRows=invocations.map(item=>`<tr><td class="col-id"><code>${escapeHtml(item.tracking_id)}</code></td><td class="col-id"><code>${escapeHtml(item.command_id)}</code></td><td class="col-status">${escapeHtml(item.send_status)} / ${escapeHtml(item.status)}</td><td class="col-number">${Number(item.reply_message_count||0)} / ${Number(item.elapsed_ms||0)}ms</td><td class="col-description"><code>${escapeHtml(item.diagnostic_code)}</code></td></tr>`).join("");
  const draft=state.groupPeerCommandDraft||emptyLegacyPeerCommandDraft(bots[0]?.user_id||"");state.groupPeerCommandDraft=draft;const validation=validateLegacyPeerCommand(draft,data.max_command_chars);const invalid=validation.error?'true':'false';
  return `<div class="card"><div class="between"><h2 style="margin:0">Peer Bot 协作</h2><button class="btn small" onclick="discoverLegacyPeerBots()" ${data.observer?.enabled?'':'disabled'}>发现一次</button></div><p class="muted">候选只由 LLM 观察产生；必须由管理员批准 Bot 与完整命令。运行摘要不展示命令正文或第三方回复原文。</p><div class="row" style="flex-wrap:wrap"><label><input id="legacy-peer-enabled" type="checkbox" ${data.enabled?'checked':''}> 启用本群调用</label><label>冷却 <input id="legacy-peer-cooldown" type="number" min="0" max="3600" value="${escapeAttr(data.policies?.cooldown_seconds??10)}" style="width:90px"></label><label>TTL <input id="legacy-peer-ttl" type="number" min="1" max="600" value="${escapeAttr(data.policies?.pending_ttl_seconds??30)}" style="width:90px"></label><button class="btn small primary" onclick="saveLegacyPeerSettings()">保存策略</button><button class="btn small danger" onclick="resetLegacyPeerLoop()">循环复位</button><span class="muted">pending ${Number(data.pending_count||0)} / 深度 1</span></div></div>
  <div class="card"><h2>识别与授权（${bots.length}）</h2><div class="table-wrap table-scroll" tabindex="0" role="region" aria-label="Peer Bot 授权"><table class="data-table xwide"><thead><tr><th scope="col">昵称</th><th scope="col">Bot ID</th><th scope="col">置信度</th><th scope="col">来源 / 状态</th><th scope="col">证据标签</th><th scope="col">操作</th></tr></thead><tbody>${botRows||'<tr><td colspan="6" class="muted">暂无候选或配置</td></tr>'}</tbody></table></div></div>
  <div class="card"><h2>完整命令模板（${commands.length}）</h2><div class="table-wrap table-scroll" tabindex="0" role="region" aria-label="Peer Bot 命令模板"><table class="data-table xwide"><thead><tr><th scope="col">命令 ID</th><th scope="col">Bot ID</th><th scope="col">完整模板</th><th scope="col">风险 / 状态</th><th scope="col">操作</th></tr></thead><tbody>${commandRows||'<tr><td colspan="5" class="muted">暂无模板</td></tr>'}</tbody></table></div><div class="structured-editor" style="margin-top:12px"><label>目标 Bot ID<input value="${escapeAttr(draft.target_bot_id)}" onchange="state.groupPeerCommandDraft.target_bot_id=this.value;render()" aria-invalid="${invalid}" aria-describedby="legacy-peer-command-help${validation.error?' legacy-peer-command-error':''}"></label><label>命令 ID<input value="${escapeAttr(draft.command_id)}" onchange="state.groupPeerCommandDraft.command_id=this.value;render()" aria-invalid="${invalid}" aria-describedby="legacy-peer-command-help${validation.error?' legacy-peer-command-error':''}"></label><label>完整模板<textarea rows="2" onchange="state.groupPeerCommandDraft.full_template=this.value;render()" aria-invalid="${invalid}" aria-describedby="legacy-peer-command-help${validation.error?' legacy-peer-command-error':''}">${escapeHtml(draft.full_template)}</textarea></label><label>参数 schema<textarea rows="7" onchange="state.groupPeerCommandDraft.parameter_schema_text=this.value;render()" aria-invalid="${invalid}" aria-describedby="legacy-peer-command-help${validation.error?' legacy-peer-command-error':''}">${escapeHtml(draft.parameter_schema_text)}</textarea></label><div class="row"><label>风险 <select onchange="state.groupPeerCommandDraft.risk_level=this.value"><option ${draft.risk_level==='read'?'selected':''}>read</option><option ${draft.risk_level==='write'?'selected':''}>write</option><option ${draft.risk_level==='admin'?'selected':''}>admin</option><option ${draft.risk_level==='dangerous'?'selected':''}>dangerous</option></select></label><label>状态 <select onchange="state.groupPeerCommandDraft.status=this.value"><option ${draft.status==='candidate'?'selected':''}>candidate</option><option ${draft.status==='approved'?'selected':''}>approved</option><option ${draft.status==='rejected'?'selected':''}>rejected</option></select></label></div><p id="legacy-peer-command-help" class="muted">占位符使用单花括号，例如 {message}；Dry-run 只在浏览器本地验证。</p>${validation.error?`<p id="legacy-peer-command-error" style="color:var(--danger)">${escapeHtml(validation.error)}</p>`:''}<label>Dry-run 参数<textarea rows="2" onchange="state.groupPeerCommandDraft.arguments_text=this.value">${escapeHtml(draft.arguments_text)}</textarea></label><div class="row"><button class="btn small" onclick="state.groupPeerDryRun=renderLegacyPeerDryRun(state.groupPeerCommandDraft,validateLegacyPeerCommand(state.groupPeerCommandDraft));render()" ${validation.error?'disabled':''}>仅验证不发送</button><button class="btn small primary" onclick="saveLegacyPeerCommand()" ${validation.error?'disabled':''}>保存模板</button><button class="btn small" onclick="state.groupPeerCommandDraft=emptyLegacyPeerCommandDraft('${escapeAttr(bots[0]?.user_id||'')}');state.groupPeerDryRun='';render()">新建</button></div>${state.groupPeerDryRun?`<output class="u-wrap" style="color:var(--ok)">${escapeHtml(state.groupPeerDryRun)}</output>`:''}</div></div>
  <div class="card"><h2>近期调用摘要（${invocations.length}）</h2><div class="table-wrap table-scroll" tabindex="0" role="region" aria-label="Peer Bot 调用摘要"><table class="data-table wide"><thead><tr><th scope="col">Tracking ID</th><th scope="col">命令 ID</th><th scope="col">状态</th><th scope="col">回复 / 耗时</th><th scope="col">诊断</th></tr></thead><tbody>${invocations.length?invocationRows:'<tr><td colspan="5" class="muted">暂无安全调用摘要</td></tr>'}</tbody></table></div></div>`;
}
