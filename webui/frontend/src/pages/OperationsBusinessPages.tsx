import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import { asRecord, BusinessTable, recordsAt, SafeStatus, textAt, type BusinessRecord } from "../components/BusinessTable";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { formatDateTime } from "../lib/format";

function operationDiagnostic(value: unknown) {
  const payload = asRecord(value);
  const nested = asRecord(payload.diagnostic);
  return safeDiagnostic(Object.keys(nested).length ? nested : payload);
}

function ActionDiagnostic({ value }: { value: unknown }) {
  if (!value) return null;
  return <DiagnosticPanel diagnostic={operationDiagnostic(value)} defaultOpen />;
}

export function UserPoliciesPage() {
  const { section = "list" } = useParams();
  const client = useQueryClient();
  const [tier, setTier] = useState("");
  const [userId, setUserId] = useState("");
  const [mode, setMode] = useState<"block" | "allow" | "inherit">("inherit");
  const [revision, setRevision] = useState(0);
  const states = useQuery({ queryKey: ["user-policies", tier], queryFn: ({ signal }) => resources.userPolicyStates(tier, signal) });
  const events = useQuery({ queryKey: ["user-policy-events", userId], queryFn: ({ signal }) => resources.userPolicyEvents(userId, signal), enabled: Boolean(userId) && section !== "list" });
  const update = useMutation({ mutationFn: () => resources.updateUserPolicy(userId, { mode, expected_revision: revision, reason_code: "webui_manual_override" }), onSuccess: () => void client.invalidateQueries({ queryKey: ["user-policies"] }) });
  const rows = recordsAt(states.data, "states", "items");
  return <div className="page-stack">
    <PageHeader index="25" title="用户策略与黑名单" description="策略状态、来源、revision 和证据分开展示；修改前必须绑定目标 QQ 与当前 revision。" actions={<select value={tier} onChange={(event) => setTier(event.target.value)} aria-label="策略等级筛选"><option value="">全部等级</option><option value="allow">允许</option><option value="blocked">阻止</option><option value="manual_allow">手工允许</option><option value="manual_block">手工阻止</option></select>} />
    <Panel eyebrow="POLICY / STATES" title="策略列表">
      <QueryBoundary isPending={states.isPending} error={states.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "user_id", "qq") + index} emptyCode="user_policy_empty" emptyText="当前没有策略记录。" columns={[
          { key: "user_id", label: "QQ ID", render: (row) => <button className="text-link" type="button" onClick={() => { setUserId(textAt(row, "user_id", "qq")); setRevision(Number(row.revision ?? 0)); }}><code>{textAt(row, "user_id", "qq")}</code></button> },
          { key: "tier", label: "等级", render: (row) => <SafeStatus row={row} keys={["tier", "mode", "state"]} /> },
          { key: "source", label: "来源", render: (row) => textAt(row, "source", "reason_code", "actor") },
          { key: "revision", label: "Revision", render: (row) => textAt(row, "revision") },
          { key: "expires_at", label: "到期", render: (row) => formatDateTime(row.expires_at as string | number | null) },
        ]} />
      </QueryBoundary>
    </Panel>
    {section !== "list" && <Panel eyebrow="POLICY / DETAIL" title={userId ? `策略详情 ${userId}` : "选择策略目标"}>
      {!userId ? <EmptyState code="user_policy_target_required">请先从策略列表选择一个 QQ。</EmptyState> : <>
        <QueryBoundary isPending={events.isPending} error={events.error}>
          <BusinessTable rows={recordsAt(events.data, "events")} rowKey={(row, index) => textAt(row, "id", "ts") + index} emptyCode="user_policy_events_empty" emptyText="该用户没有保留中的策略事件。" columns={[
            { key: "ts", label: "时间", render: (row) => formatDateTime(row.ts as string | number | null) },
            { key: "action", label: "事件", render: (row) => textAt(row, "action", "event_type", "reason_code") },
            { key: "source", label: "证据来源", render: (row) => textAt(row, "source_kind", "source", "actor") },
            { key: "outcome", label: "结果", render: (row) => <SafeStatus row={row} /> },
          ]} />
        </QueryBoundary>
        {section === "edit" && <div className="inline-controls filter-control-row">
          <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)} aria-label="手工策略"><option value="inherit">继承自动策略</option><option value="allow">手工允许</option><option value="block">手工阻止</option></select>
          <input type="number" min={0} value={revision} onChange={(event) => setRevision(Number(event.target.value))} aria-label="期望 revision" />
          <button className="button button-primary" type="button" disabled={update.isPending} onClick={() => { if (window.confirm(`确认把 QQ ${userId} 的策略修改为 ${mode}，revision=${revision}？`)) update.mutate(); }}>保存策略</button>
        </div>}
      </>}
    </Panel>}
    <ActionDiagnostic value={update.data} />
  </div>;
}

export function OutboundMessagesPage() {
  const { section = "list" } = useParams();
  const [selected, setSelected] = useState<BusinessRecord | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const query = useQuery({ queryKey: ["outbound-recent"], queryFn: ({ signal }) => resources.outboundRecent(signal) });
  const recall = useMutation({ mutationFn: (row: BusinessRecord) => resources.recallOutbound(textAt(row, "operation_id"), { bot_id: textAt(row, "bot_id"), conversation_kind: textAt(row, "conversation_kind", "session_type"), conversation_id: textAt(row, "conversation_id", "session_id"), confirmation }) });
  const rows = recordsAt(query.data, "messages", "items");
  return <div className="page-stack">
    <PageHeader index="26" title="近期 Bot 消息" description="按 operation 展示发送证据与 Trace；撤回要求精确确认串，unknown/partial 结果不会出现自动重试入口。" />
    <Panel eyebrow="OUTBOUND / LEDGER" title="出站记录">
      <QueryBoundary isPending={query.isPending} error={query.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "id", "operation_id") + index} emptyCode="outbound_empty" emptyText="当前没有出站账本记录。" columns={[
          { key: "created_at", label: "时间", render: (row) => formatDateTime((row.created_at ?? row.ts) as string | number | null) },
          { key: "operation_id", label: "Operation", render: (row) => <button className="text-link" type="button" onClick={() => { setSelected(row); setConfirmation(""); }}><code>{textAt(row, "operation_id")}</code></button> },
          { key: "conversation", label: "会话", render: (row) => `${textAt(row, "conversation_kind", "session_type")} / ${textAt(row, "conversation_id", "session_id")}` },
          { key: "status", label: "发送结果", render: (row) => <SafeStatus row={row} /> },
          { key: "trace_id", label: "Trace", render: (row) => <code>{textAt(row, "trace_id")}</code> },
        ]} />
      </QueryBoundary>
    </Panel>
    {selected && <Panel eyebrow="OUTBOUND / EVIDENCE" title={`Operation ${textAt(selected, "operation_id")}`}>
      <dl className="detail-list"><div><dt>Bot</dt><dd>{textAt(selected, "bot_id")}</dd></div><div><dt>会话</dt><dd>{textAt(selected, "conversation_kind", "session_type")} / {textAt(selected, "conversation_id", "session_id")}</dd></div><div><dt>平台消息数</dt><dd>{textAt(selected, "message_count", "segment_count")}</dd></div><div><dt>结果</dt><dd><SafeStatus row={selected} /></dd></div></dl>
      {section === "detail" && <div className="danger-zone"><p>要撤回，输入 <code>RECALL {textAt(selected, "operation_id")}</code>。结果未知时界面不会再次提交。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} aria-label="撤回确认串" /><button className="button button-danger" type="button" disabled={confirmation !== `RECALL ${textAt(selected, "operation_id")}` || recall.isPending} onClick={() => recall.mutate(selected)}>撤回完整 operation</button></div>}
    </Panel>}
    <ActionDiagnostic value={recall.data} />
  </div>;
}

export function AuditLogPage() {
  const { section = "records" } = useParams();
  const [action, setAction] = useState("");
  const [selected, setSelected] = useState<BusinessRecord | null>(null);
  const actions = useQuery({ queryKey: ["audit-actions"], queryFn: ({ signal }) => resources.auditActions(signal) });
  const records = useQuery({ queryKey: ["audit-records", action], queryFn: ({ signal }) => resources.auditRecent(action, signal) });
  const rows = recordsAt(records.data, "entries", "items");
  return <div className="page-stack">
    <PageHeader index="28" title="审计日志" description="游标式读取管理员操作、目标、结果和脱敏详情；完整请求体、密钥和 Cookie 不进入此页面。" actions={<select value={action} onChange={(event) => setAction(event.target.value)} aria-label="审计动作筛选"><option value="">全部动作</option>{recordsAt(actions.data, "actions").map((row) => <option key={textAt(row, "key")} value={textAt(row, "key")}>{textAt(row, "label", "key")}</option>)}</select>} />
    <Panel eyebrow="AUDIT / RECORDS" title={section === "overview" ? "操作概览" : "审计记录"}>
      <QueryBoundary isPending={records.isPending} error={records.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "id", "ts") + index} emptyCode="audit_empty" emptyText="当前筛选条件下没有审计记录。" columns={[
          { key: "ts", label: "时间", render: (row) => formatDateTime(row.ts as string | number | null) },
          { key: "action", label: "动作", render: (row) => <button className="text-link" type="button" onClick={() => setSelected(row)}>{textAt(row, "action")}</button> },
          { key: "qq", label: "管理员", render: (row) => <code>{textAt(row, "qq", "admin_qq")}</code> },
          { key: "target", label: "目标", render: (row) => textAt(row, "target") },
          { key: "outcome", label: "结果", render: (row) => <SafeStatus row={row} /> },
        ]} />
      </QueryBoundary>
    </Panel>
    {(section === "detail" || selected) && selected && <Panel eyebrow="AUDIT / SAFE DETAIL" title="脱敏详情"><dl className="detail-list"><div><dt>动作</dt><dd>{textAt(selected, "action")}</dd></div><div><dt>目标</dt><dd>{textAt(selected, "target")}</dd></div><div><dt>设备摘要</dt><dd>{textAt(selected, "device_id", "device_hash")}</dd></div><div><dt>诊断码</dt><dd>{textAt(asRecord(selected.detail), "code", "diagnostic_code")}</dd></div></dl></Panel>}
  </div>;
}

export function QqManagementPage() {
  const { section = "accounts" } = useParams();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [nickname, setNickname] = useState("");
  const [signature, setSignature] = useState("");
  const [targetId, setTargetId] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const path = section === "groups" ? "groups" : section === "friends" ? "friends" : "info";
  const query = useQuery({ queryKey: ["qq-management", path], queryFn: ({ signal }) => resources.qqGet(path, signal) });
  const profileMutation = useMutation({ mutationFn: ({ kind, value }: { kind: "nickname" | "signature"; value: string }) => resources.qqPost(kind, { [kind]: value }), onSuccess: () => void client.invalidateQueries({ queryKey: ["qq-management"] }) });
  const dangerousMutation = useMutation({ mutationFn: ({ kind, id }: { kind: "group" | "friend"; id: string }) => kind === "group" ? resources.qqPost(`groups/${encodeURIComponent(id)}/leave`, { confirm: confirmation, is_dismiss: false }) : resources.qqDelete(`friends/${encodeURIComponent(id)}`, { confirm: confirmation }) });
  const allRows = section === "groups" ? recordsAt(query.data, "groups") : section === "friends" ? recordsAt(query.data, "friends") : [asRecord(query.data)];
  const rows = allRows.filter((row) => `${textAt(row, "group_id", "user_id")} ${textAt(row, "group_name", "nickname", "remark")}`.toLocaleLowerCase("zh-CN").includes(search.toLocaleLowerCase("zh-CN")));
  return <div className="page-stack">
    <PageHeader index="30" title="QQ 管理" description="账号、群和好友使用专用视图；外部写操作绑定目标 ID，服务端三态结果未知时不会自动重试。" actions={section !== "accounts" && section !== "profile" ? <SearchField value={search} onChange={setSearch} placeholder="搜索 ID 或名称" /> : undefined} />
    {(section === "accounts" || section === "profile") && <Panel eyebrow="QQ / PROFILE" title="Bot 账号与资料操作">
      <QueryBoundary isPending={query.isPending} error={query.error}><dl className="detail-list"><div><dt>Bot QQ</dt><dd><code>{textAt(asRecord(query.data), "user_id")}</code></dd></div><div><dt>昵称</dt><dd>{textAt(asRecord(query.data), "nickname")}</dd></div></dl></QueryBoundary>
      {section === "profile" && <div className="stacked-form"><label>新昵称<input value={nickname} onChange={(event) => setNickname(event.target.value)} /></label><button className="button button-primary" type="button" disabled={!nickname.trim() || profileMutation.isPending} onClick={() => { if (window.confirm(`确认把 Bot 昵称修改为“${nickname}”？`)) profileMutation.mutate({ kind: "nickname", value: nickname }); }}>修改昵称</button><label>新签名<textarea value={signature} onChange={(event) => setSignature(event.target.value)} /></label><button className="button button-primary" type="button" disabled={profileMutation.isPending} onClick={() => { if (window.confirm("确认修改当前 Bot 的个性签名？")) profileMutation.mutate({ kind: "signature", value: signature }); }}>修改签名</button></div>}
    </Panel>}
    {(section === "groups" || section === "friends") && <Panel eyebrow={`QQ / ${section.toUpperCase()}`} title={section === "groups" ? "已知群目录" : "好友目录"}>
      <QueryBoundary isPending={query.isPending} error={query.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "group_id", "user_id") + index} emptyCode={`qq_${section}_empty`} emptyText="当前没有可展示的目录记录。" columns={[
          { key: "id", label: "ID", render: (row) => <code>{textAt(row, "group_id", "user_id")}</code> },
          { key: "name", label: "名称", render: (row) => textAt(row, "group_name", "nickname", "remark") },
          { key: "count", label: section === "groups" ? "成员数" : "备注", render: (row) => textAt(row, "member_count", "remark") },
          { key: "danger", label: "危险操作", render: (row) => <button className="button button-danger" type="button" onClick={() => { setTargetId(textAt(row, "group_id", "user_id")); setConfirmation(""); }}>{section === "groups" ? "准备退群" : "准备删除"}</button> },
        ]} />
      </QueryBoundary>
    </Panel>}
    {targetId && <Panel eyebrow="QQ / EXTERNAL WRITE" title="外部写操作二次核对"><p>目标：<code>{targetId}</code>。请输入目标 ID 才能继续；提交后如果结果未知，页面不会自动再次调用。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} aria-label="QQ 操作目标确认" /><button className="button button-danger" type="button" disabled={confirmation !== targetId || dangerousMutation.isPending} onClick={() => dangerousMutation.mutate({ kind: section === "groups" ? "group" : "friend", id: targetId })}>确认执行</button></Panel>}
    <ActionDiagnostic value={profileMutation.data ?? dangerousMutation.data} />
  </div>;
}

export function DeviceManagementPage() {
  const { section = "current" } = useParams();
  const client = useQueryClient();
  const [targetId, setTargetId] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const endpoint = section === "pending" ? "pending-devices" : section === "trusted" ? "trusted-devices" : "devices";
  const query = useQuery({ queryKey: ["devices", endpoint], queryFn: ({ signal }) => resources.deviceGet(endpoint, signal) });
  const action = useMutation({ mutationFn: ({ id, kind }: { id: string; kind: "approve" | "revoke" | "untrust" }) => kind === "approve" ? resources.devicePost(`devices/${encodeURIComponent(id)}/approve`) : kind === "untrust" ? resources.deviceDelete(`trusted-devices/${encodeURIComponent(id)}`) : resources.deviceDelete(`devices/${encodeURIComponent(id)}`), onSuccess: () => void client.invalidateQueries({ queryKey: ["devices"] }) });
  const rows = recordsAt(query.data, "devices", "items");
  const currentId = textAt(asRecord(query.data), "current_device_id");
  return <div className="page-stack">
    <PageHeader index="31" title="设备管理" description="当前、已授权、待审批和历史信任设备分开显示；审批和撤销绑定设备 ID 并写入审计。" />
    <Panel eyebrow={`DEVICES / ${section.toUpperCase()}`} title={section === "pending" ? "待审批设备" : section === "trusted" ? "历史信任设备" : section === "current" ? "当前设备" : "已授权设备"}>
      {section === "current" && <p>当前设备 ID：<code>{currentId}</code></p>}
      <QueryBoundary isPending={query.isPending} error={query.error}>
        <BusinessTable rows={section === "current" ? rows.filter((row) => textAt(row, "id") === currentId) : rows} rowKey={(row, index) => textAt(row, "id") + index} emptyCode={`devices_${section}_empty`} emptyText="当前分类没有设备。" columns={[
          { key: "id", label: "设备 ID", render: (row) => <code>{textAt(row, "id")}</code> },
          { key: "label", label: "名称", render: (row) => textAt(row, "label") },
          { key: "ua", label: "浏览器摘要", render: (row) => textAt(row, "ua") },
          { key: "status", label: "状态", render: (row) => <SafeStatus row={row} /> },
          { key: "action", label: "操作", render: (row) => <button className={section === "pending" ? "button button-primary" : "button button-danger"} type="button" onClick={() => { setTargetId(textAt(row, "id")); setConfirmation(""); }}>{section === "pending" ? "准备批准" : section === "trusted" ? "准备移除信任" : "准备撤销"}</button> },
        ]} />
      </QueryBoundary>
    </Panel>
    {targetId && <Panel eyebrow="DEVICES / CONFIRM" title="核对设备目标"><p>请输入设备 ID <code>{targetId}</code>。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button className="button button-danger" type="button" disabled={confirmation !== targetId || action.isPending} onClick={() => action.mutate({ id: targetId, kind: section === "pending" ? "approve" : section === "trusted" ? "untrust" : "revoke" })}>确认操作</button></Panel>}
    <ActionDiagnostic value={action.data} />
  </div>;
}

export function DataTransferPage() {
  const { section = "export" } = useParams();
  const [botId, setBotId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [journalId, setJournalId] = useState("");
  const [planToken, setPlanToken] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const exportMutation = useMutation({ mutationFn: () => resources.createStateExport({ bot_id: botId, group_id: groupId, datasets: [] }) });
  const uploadMutation = useMutation({ mutationFn: () => resources.uploadStateImport(file as File), onSuccess: (value) => setTaskId(textAt(asRecord(value), "task_id")) });
  const inspect = useQuery({ queryKey: ["data-transfer-inspect", taskId], queryFn: ({ signal }) => resources.inspectImport(taskId, signal), enabled: section !== "export" && Boolean(taskId) });
  const dryRun = useMutation({ mutationFn: () => resources.dryRunImport(taskId, { target_bot_id: botId, target_group_id: groupId, mode: "merge", allow_same_identity: false }), onSuccess: (value) => setPlanToken(textAt(asRecord(value), "plan_token")) });
  const apply = useMutation({ mutationFn: () => resources.applyImport(taskId, { target_bot_id: botId, target_group_id: groupId, mode: "merge", allow_same_identity: false, plan_token: planToken }), onSuccess: (value) => setJournalId(textAt(asRecord(value), "journal_id")) });
  const rollback = useMutation({ mutationFn: () => resources.rollbackImport(journalId) });
  const latestResult = exportMutation.data ?? uploadMutation.data ?? dryRun.data ?? apply.data ?? rollback.data;
  return <div className="page-stack">
    <PageHeader index="27" title="数据迁移" description="导出、上传、inspect、dry-run、apply、journal 与 rollback 使用同一服务；秘密包在公网 HTTP 下仍由后端拒绝。" />
    <Panel eyebrow={`TRANSFER / ${section.toUpperCase()}`} title={section === "export" ? "导出群安全状态包" : section === "inspect" ? "上传与验包" : section === "apply" ? "应用已预演计划" : "Journal 与回滚"}>
      <div className="stacked-form">
        <label>Bot ID<input value={botId} onChange={(event) => setBotId(event.target.value)} /></label>
        <label>群 ID<input value={groupId} onChange={(event) => setGroupId(event.target.value)} /></label>
        {section === "export" && <button className="button button-primary" type="button" disabled={!botId || !groupId || exportMutation.isPending} onClick={() => { if (window.confirm(`确认导出 Bot ${botId} / 群 ${groupId} 的群安全状态包？`)) exportMutation.mutate(); }}>创建导出</button>}
        {section === "inspect" && <><label>迁移包<input type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><button className="button button-primary" type="button" disabled={!file || uploadMutation.isPending} onClick={() => uploadMutation.mutate()}>上传并安全验包</button><label>Task ID<input value={taskId} onChange={(event) => setTaskId(event.target.value)} /></label><button className="button button-secondary" type="button" disabled={!taskId || !botId || !groupId || dryRun.isPending} onClick={() => dryRun.mutate()}>执行 Dry-run</button></>}
        {section === "apply" && <><label>Task ID<input value={taskId} onChange={(event) => setTaskId(event.target.value)} /></label><label>Plan Token<input value={planToken} onChange={(event) => setPlanToken(event.target.value)} /></label><p>输入 <code>APPLY {taskId}</code> 才能应用。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button className="button button-danger" type="button" disabled={confirmation !== `APPLY ${taskId}` || !planToken || apply.isPending} onClick={() => apply.mutate()}>应用导入</button></>}
        {section === "journal" && <><label>Journal ID<input value={journalId} onChange={(event) => setJournalId(event.target.value)} /></label><p>输入 <code>ROLLBACK {journalId}</code> 才能回滚。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button className="button button-danger" type="button" disabled={confirmation !== `ROLLBACK ${journalId}` || rollback.isPending} onClick={() => rollback.mutate()}>回滚本次导入</button></>}
      </div>
      {inspect.data && section === "inspect" && <dl className="detail-list"><div><dt>任务 ID</dt><dd>{taskId}</dd></div><div><dt>Schema</dt><dd>{textAt(asRecord(inspect.data), "schema_version")}</dd></div><div><dt>源 Bot</dt><dd>{textAt(asRecord(inspect.data), "bot_id", "source_bot_id")}</dd></div><div><dt>源群</dt><dd>{textAt(asRecord(inspect.data), "group_id", "source_group_id")}</dd></div></dl>}
    </Panel>
    <ActionDiagnostic value={latestResult} />
  </div>;
}
