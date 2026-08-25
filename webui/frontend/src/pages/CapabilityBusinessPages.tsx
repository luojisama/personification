import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { CatalogItem, Page, PluginUpdateOperation, PluginUpdateStatus } from "../api/types";
import { asRecord, BusinessTable, recordsAt, SafeStatus, textAt, type BusinessRecord } from "../components/BusinessTable";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime } from "../lib/format";

function diagnostics(value: unknown) {
  if (!value) return null;
  const row = asRecord(value);
  const diagnostic = asRecord(row.diagnostic);
  if (Object.keys(diagnostic).length) return safeDiagnostic(diagnostic);

  const operation = asRecord(row.operation);
  const code = textAt(row, "code", "diagnostic_code") !== "—"
    ? textAt(row, "code", "diagnostic_code")
    : textAt(operation, "diagnostic_code");
  if (code === "—") return null;

  const state = textAt(operation, "state");
  const ok = row.ok === true || ["ready", "succeeded"].includes(state);
  return safeDiagnostic({
    ok,
    code,
    phase: textAt(row, "phase") === "—" ? "operation_complete" : textAt(row, "phase"),
    message: textAt(row, "message", "error") === "—"
      ? ok ? "服务端已确认操作结果。" : "操作未完成，请依据诊断码核对。"
      : textAt(row, "message", "error"),
    operation_id: textAt(operation, "operation_id") === "—" ? undefined : textAt(operation, "operation_id"),
    retryable: false,
    partial: false,
    outcome_unknown: state === "unknown",
    warnings: [],
    steps: [],
  });
}

function ResultPanel({ value }: { value: unknown }) {
  const valueDiagnostic = diagnostics(value);
  return valueDiagnostic ? <DiagnosticPanel diagnostic={valueDiagnostic} defaultOpen /> : null;
}

export function SkillsPage() {
  const { section = "installed" } = useParams();
  const client = useQueryClient();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const query = useQuery<Page<CatalogItem>>({ queryKey: ["skills", page, search], queryFn: ({ signal }) => resources.catalog("skills", page, 20, search, signal) });
  const toggle = useMutation({ mutationFn: ({ name, disabled }: { name: string; disabled: boolean }) => resources.skillAction(`${encodeURIComponent(name)}/toggle`, { disabled, reason: "webui_explicit_toggle" }), onSuccess: () => void client.invalidateQueries({ queryKey: ["skills"] }) });
  const reload = useMutation({ mutationFn: () => resources.skillAction("reload") });
  const remoteToggle = useMutation({ mutationFn: (enabled: boolean) => resources.skillAction("remote/toggle", { enabled }) });
  const rows = query.data?.items ?? [];
  return <div className="page-stack">
    <PageHeader index="19" title="Skill 管理" description="已安装 Skill、远程源、审核状态和健康信息分层展示；启停、远程加载与重载都有明确诊断。" actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="搜索 Skill 名称或描述" />} />
    {section === "remote" && <Panel eyebrow="SKILL / REMOTE" title="远程来源控制"><p>远程来源仍需内容 digest 审核；打开加载开关不会自动批准或执行外部代码。</p><div className="inline-controls"><button className="button button-primary" type="button" onClick={() => { if (window.confirm("确认开启远程 Skill 加载？仍需单独审核来源。")) remoteToggle.mutate(true); }}>开启远程加载</button><button className="button button-secondary" type="button" onClick={() => remoteToggle.mutate(false)}>关闭远程加载</button></div></Panel>}
    <Panel eyebrow={`SKILL / ${section.toUpperCase()}`} title={section === "health" ? "健康与审核" : "已安装 Skill"} action={<button className="button button-secondary" type="button" disabled={reload.isPending} onClick={() => { if (window.confirm("确认重载 Skill runtime？")) reload.mutate(); }}>重载 Runtime</button>}>
      <QueryBoundary isPending={query.isPending} error={query.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "name") + index} emptyCode="skills_empty" emptyText="当前没有匹配的 Skill。" columns={[
          { key: "name", label: "Skill", render: (row) => <><strong>{textAt(row, "name")}</strong><br /><span className="muted">{textAt(row, "description")}</span></> },
          { key: "source", label: "来源", render: (row) => textAt(row, "source_kind", "category") },
          { key: "risk", label: "副作用", render: (row) => textAt(row, "risk", "side_effect_level", "permission") },
          { key: "status", label: "状态", render: (row) => <StateBadge tone={row.user_disabled === true || row.health_disabled === true ? "error" : "ok"}>{row.user_disabled === true ? "用户禁用" : row.health_disabled === true ? "健康禁用" : "已启用"}</StateBadge> },
          { key: "action", label: "操作", render: (row) => { const disabled = row.user_disabled === true; const name = textAt(row, "name"); return <button className="button button-secondary" type="button" disabled={toggle.isPending} onClick={() => { if (window.confirm(`确认${disabled ? "启用" : "禁用"} Skill ${name}？`)) toggle.mutate({ name, disabled: !disabled }); }}>{disabled ? "启用" : "禁用"}</button>; } },
        ]} />
      </QueryBoundary>
      {query.data && <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={setPage} />}
    </Panel>
    <ResultPanel value={toggle.data ?? reload.data ?? remoteToggle.data} />
  </div>;
}

export function McpManagementPage() {
  const { section = "registry" } = useParams();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<BusinessRecord | null>(null);
  const endpoint = section === "installations" ? "installations" : section === "social" || section === "review" ? "builtin/social-research/status" : "search";
  const query = useQuery({ queryKey: ["mcp-management", endpoint, search], queryFn: ({ signal }) => endpoint === "search" ? resources.mcpGet(endpoint, { q: search, source_id: "official", limit: 20 }, signal) : resources.mcpGet(endpoint, {}, signal) });
  const reload = useMutation({ mutationFn: () => resources.mcpPost("reload"), onSuccess: () => void client.invalidateQueries({ queryKey: ["mcp-management"] }) });
  const toggle = useMutation({ mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => resources.mcpPost(`installations/${encodeURIComponent(id)}/toggle`, { enabled }), onSuccess: () => void client.invalidateQueries({ queryKey: ["mcp-management"] }) });
  const install = useMutation({ mutationFn: (row: BusinessRecord) => resources.mcpPost("install", { source_id: textAt(row, "source_id") === "—" ? "official" : textAt(row, "source_id"), name: textAt(row, "name", "server_name"), confirm_execution: true }), onSuccess: () => void client.invalidateQueries({ queryKey: ["mcp-management"] }) });
  const rows = section === "installations" ? recordsAt(query.data, "installations") : section === "registry" ? recordsAt(query.data, "servers", "items", "results") : [asRecord(query.data)].filter((row) => Object.keys(row).length > 0);
  return <div className="page-stack">
    <PageHeader index="20" title="MCP 管理" description="Registry、安装实例、内置社交研究、授权与语义审核保持独立状态；发现工具不会执行工具。" actions={section === "registry" ? <SearchField value={search} onChange={setSearch} placeholder="搜索 MCP Registry" /> : undefined} />
    <Panel eyebrow={`MCP / ${section.toUpperCase()}`} title={section === "registry" ? "Registry" : section === "installations" ? "安装实例" : section === "social" ? "内置社交研究" : "授权与语义审核"} action={<button className="button button-secondary" type="button" disabled={reload.isPending} onClick={() => { if (window.confirm("确认重载 MCP process 与工具目录？")) reload.mutate(); }}>重载 MCP</button>}>
      <QueryBoundary isPending={query.isPending} error={query.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "installation_id", "name", "server_name", "platform") + index} emptyCode={`mcp_${section}_empty`} emptyText="当前没有对应的 MCP 记录。" columns={[
          { key: "name", label: "Server / 平台", render: (row) => <button className="text-link" type="button" onClick={() => setSelected(row)}><strong>{textAt(row, "name", "server_name", "platform", "service")}</strong><br /><code>{textAt(row, "installation_id", "source_id")}</code></button> },
          { key: "description", label: "说明", render: (row) => textAt(row, "description", "summary", "last_error") },
          { key: "status", label: "运行与授权", render: (row) => <SafeStatus row={row} keys={["status", "state", "auth_state", "process_state"]} /> },
          { key: "tools", label: "工具数", render: (row) => textAt(row, "tool_count", "enabled_tools", "tools_count") },
          { key: "action", label: "操作", render: (row) => section === "registry" ? <button className="button button-primary" type="button" disabled={install.isPending} onClick={() => { if (window.confirm(`确认安装 MCP ${textAt(row, "name", "server_name")}？安装可能启动本地进程。`)) install.mutate(row); }}>安装</button> : section === "installations" ? <button className="button button-secondary" type="button" disabled={toggle.isPending} onClick={() => toggle.mutate({ id: textAt(row, "installation_id"), enabled: row.desired_enabled !== true })}>{row.desired_enabled === true ? "停用" : "启用"}</button> : <span className="muted">使用专用授权流程</span> },
        ]} />
      </QueryBoundary>
    </Panel>
    {selected && <Panel eyebrow="MCP / DETAIL" title="实例安全摘要"><dl className="detail-list"><div><dt>实例 ID</dt><dd><code>{textAt(selected, "installation_id")}</code></dd></div><div><dt>来源</dt><dd>{textAt(selected, "source_id", "source")}</dd></div><div><dt>命令类型</dt><dd>{textAt(selected, "transport", "command_type")}</dd></div><div><dt>最后错误</dt><dd>{textAt(selected, "last_error")}</dd></div></dl></Panel>}
    <ResultPanel value={reload.data ?? toggle.data ?? install.data} />
  </div>;
}

export function ToolCreatorPage() {
  const { section = "tasks" } = useParams();
  const client = useQueryClient();
  const [request, setRequest] = useState("");
  const [suggestedName, setSuggestedName] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const query = useQuery({ queryKey: ["tool-creator-tasks"], queryFn: ({ signal }) => resources.toolCreatorGet("tasks", signal) });
  const detail = useQuery({ queryKey: ["tool-creator-detail", selectedId], queryFn: ({ signal }) => resources.toolCreatorGet(`tasks/${encodeURIComponent(selectedId)}`, signal), enabled: Boolean(selectedId) && section !== "tasks" });
  const create = useMutation({ mutationFn: () => resources.toolCreatorPost("tasks", { request, suggested_name: suggestedName }), onSuccess: () => void client.invalidateQueries({ queryKey: ["tool-creator-tasks"] }) });
  const lifecycle = useMutation({ mutationFn: ({ action, task }: { action: "cancel" | "retry"; task: BusinessRecord }) => resources.toolCreatorPost(`tasks/${encodeURIComponent(textAt(task, "task_id"))}/${action}`, { expected_version: Number(task.version ?? 0) }), onSuccess: () => void client.invalidateQueries({ queryKey: ["tool-creator-tasks"] }) });
  const rows = recordsAt(query.data, "tasks");
  return <div className="page-stack">
    <PageHeader index="21" title="创建工具" description="任务、管理员问题、事件、产物和验证结果按生命周期展示；副作用级别在批准前必须可见。" />
    {section === "tasks" && <Panel eyebrow="TOOL CREATOR / NEW" title="创建声明式工具任务"><div className="stacked-form"><label>建议名称<input value={suggestedName} onChange={(event) => setSuggestedName(event.target.value)} /></label><label>需求<textarea value={request} onChange={(event) => setRequest(event.target.value)} /></label><button className="button button-primary" type="button" disabled={!request.trim() || create.isPending} onClick={() => { if (window.confirm("确认创建工具生成任务？产物仍需验证和批准后才发布。")) create.mutate(); }}>创建任务</button></div></Panel>}
    <Panel eyebrow={`TOOL CREATOR / ${section.toUpperCase()}`} title="任务列表">
      <QueryBoundary isPending={query.isPending} error={query.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "task_id") + index} emptyCode="tool_creator_empty" emptyText="当前没有工具创建任务。" columns={[
          { key: "task", label: "任务", render: (row) => <button className="text-link" type="button" onClick={() => setSelectedId(textAt(row, "task_id"))}><strong>{textAt(row, "suggested_name")}</strong><br /><code>{textAt(row, "task_id")}</code></button> },
          { key: "request", label: "安全摘要", render: (row) => textAt(row, "request_text", "summary") },
          { key: "risk", label: "副作用", render: (row) => textAt(row, "side_effect_level", "risk") },
          { key: "status", label: "阶段", render: (row) => <SafeStatus row={row} /> },
          { key: "action", label: "操作", render: (row) => <div className="inline-controls"><button className="button button-secondary" type="button" disabled={lifecycle.isPending} onClick={() => lifecycle.mutate({ action: "retry", task: row })}>继续 / 重试</button><button className="button button-danger" type="button" disabled={lifecycle.isPending} onClick={() => { if (window.confirm(`确认取消任务 ${textAt(row, "task_id")}？`)) lifecycle.mutate({ action: "cancel", task: row }); }}>取消</button></div> },
        ]} />
      </QueryBoundary>
    </Panel>
    {selectedId && section !== "tasks" && <Panel eyebrow="TOOL CREATOR / DETAIL" title={`任务详情 ${selectedId}`}><QueryBoundary isPending={detail.isPending} error={detail.error}><BusinessTable rows={recordsAt(detail.data, "events", "questions", "artifacts")} rowKey={(row, index) => textAt(row, "id", "question_id", "digest", "ts") + index} emptyCode="tool_creator_detail_empty" emptyText="当前任务没有该类记录。" columns={[
      { key: "kind", label: "类型", render: (row) => textAt(row, "kind", "type", "event", "question") }, { key: "summary", label: "内容 / 产物", render: (row) => textAt(row, "summary", "message", "prompt", "path") }, { key: "status", label: "验证", render: (row) => <SafeStatus row={row} /> },
    ]} /></QueryBoundary></Panel>}
    <ResultPanel value={create.data ?? lifecycle.data} />
  </div>;
}

export function PluginKnowledgePage() {
  const { section = "catalog" } = useParams();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [selectedName, setSelectedName] = useState("");
  const catalog = useQuery<Page<CatalogItem>>({ queryKey: ["plugin-knowledge", page, search], queryFn: ({ signal }) => resources.catalog("plugin-knowledge", page, 20, search, signal), enabled: section !== "search" || !search.trim() });
  const searchQuery = useQuery({ queryKey: ["plugin-knowledge-search", search], queryFn: ({ signal }) => resources.pluginKnowledgeSearch(search, signal), enabled: section === "search" && Boolean(search.trim()) });
  const detail = useQuery({ queryKey: ["plugin-knowledge-detail", selectedName], queryFn: ({ signal }) => resources.pluginKnowledgeDetail(selectedName, signal), enabled: Boolean(selectedName) });
  const rows = section === "search" ? recordsAt(searchQuery.data, "results", "items") : catalog.data?.items ?? [];
  return <div className="page-stack">
    <PageHeader index="22" title="插件知识库" description="知识目录、语义搜索、覆盖率和源文件采用业务字段展示；完整源码与未清洗快照只在服务端受控读取。" actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="搜索插件名、命令或能力" />} />
    <Panel eyebrow={`PLUGIN KNOWLEDGE / ${section.toUpperCase()}`} title={section === "search" ? "语义搜索" : section === "rebuild" ? "索引状态与重建" : "知识目录"}>
      <QueryBoundary isPending={catalog.isPending || searchQuery.isPending} error={catalog.error ?? searchQuery.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "plugin_name", "name") + index} emptyCode="plugin_knowledge_empty" emptyText="当前没有匹配的插件知识。" columns={[
          { key: "plugin", label: "插件", render: (row) => <button className="text-link" type="button" onClick={() => setSelectedName(textAt(row, "plugin_name", "name"))}><strong>{textAt(row, "display_name", "plugin_name", "name")}</strong><br /><code>{textAt(row, "plugin_name", "name")}</code></button> },
          { key: "summary", label: "用途摘要", render: (row) => textAt(row, "summary", "description", "analysis_scope") },
          { key: "category", label: "分类", render: (row) => textAt(row, "category") },
          { key: "coverage", label: "覆盖率", render: (row) => textAt(row, "coverage", "source_coverage", "command_count") },
        ]} />
      </QueryBoundary>
      {catalog.data && section !== "search" && <Pagination page={catalog.data.page} totalPages={catalog.data.total_pages} onChange={setPage} />}
    </Panel>
    {selectedName && <Panel eyebrow="PLUGIN KNOWLEDGE / DETAIL" title={selectedName}><QueryBoundary isPending={detail.isPending} error={detail.error}><dl className="detail-list"><div><dt>分类</dt><dd>{textAt(asRecord(asRecord(detail.data).entry), "category")}</dd></div><div><dt>摘要</dt><dd>{textAt(asRecord(asRecord(detail.data).entry), "summary", "description")}</dd></div><div><dt>来源覆盖</dt><dd>{textAt(asRecord(detail.data), "source_coverage")}</dd></div><div><dt>诊断码</dt><dd>{textAt(asRecord(asRecord(detail.data).diagnostic), "code")}</dd></div></dl></QueryBoundary></Panel>}
  </div>;
}

function UpdateSourceTable({ operation }: { operation: PluginUpdateOperation | undefined }) {
  const probes = operation?.probes ?? [];
  const selected = probes.find((probe) => probe.source_id === operation?.selected_source_id);
  return <>
    <BusinessTable rows={probes as unknown as BusinessRecord[]} rowKey={(row, index) => textAt(row, "source_id") + index} emptyCode="plugin_update_probes_empty" emptyText="尚未执行五源真实 Git 测速。" columns={[
      { key: "rank", label: "排名", render: (row) => row.rank ? `#${String(row.rank)}` : "—" },
      { key: "display_name", label: "更新源", render: (row) => <><strong>{textAt(row, "display_name")}</strong><br /><code>{textAt(row, "kind")}</code></> },
      { key: "latency_ms", label: "延迟", render: (row) => row.latency_ms == null ? "—" : `${String(row.latency_ms)} ms` },
      { key: "state", label: "Git 探测", render: (row) => <SafeStatus row={row} /> },
      { key: "diagnostic_code", label: "诊断码", render: (row) => <code>{textAt(row, "diagnostic_code")}</code> },
    ]} />
    {operation?.selected_source_id && <p className="muted">本次选中源：<strong>{selected?.display_name ?? operation.selected_source_id}</strong> · <code>{operation.selected_source_id}</code></p>}
  </>;
}

export function PluginManagementPage() {
  const { section = "status" } = useParams();
  const client = useQueryClient();
  const [confirmation, setConfirmation] = useState("");
  const statusQuery = useQuery<PluginUpdateStatus>({ queryKey: ["plugin-update-status"], queryFn: ({ signal }) => resources.pluginUpdateStatus(signal), refetchInterval: false });
  const historyQuery = useQuery({ queryKey: ["plugin-update-history"], queryFn: ({ signal }) => resources.pluginUpdateHistory(signal), enabled: section === "history" });
  const benchmark = useMutation({ mutationFn: () => resources.pluginUpdateBenchmark(), onSuccess: () => void client.invalidateQueries({ queryKey: ["plugin-update-status"] }) });
  const check = useMutation({ mutationFn: () => resources.pluginUpdateCheck(), onSuccess: () => void client.invalidateQueries({ queryKey: ["plugin-update-status"] }) });
  const apply = useMutation({ mutationFn: () => resources.pluginUpdateApply(), onSuccess: () => { setConfirmation(""); void client.invalidateQueries({ queryKey: ["plugin-update-status"] }); } });
  const status = statusQuery.data;
  const operation = apply.data?.operation ?? check.data?.operation ?? benchmark.data?.operation ?? status?.operation;
  const history = historyQuery.data?.items ?? [];
  return <div className="page-stack">
    <PageHeader index="23" title="插件管理" description="命令与 WebUI 共用真实 Git 五源测速、最快源选择、网络失败按排名回退和本地 fast-forward；不会修改 remote 或 Git 配置。" />
    <Panel eyebrow="PLUGIN / SOURCE" title="当前版本与仓库">
      <QueryBoundary isPending={statusQuery.isPending} error={statusQuery.error}>
        {status ? <dl className="detail-list"><div><dt>分支 / Upstream</dt><dd>{status.local.branch ?? "—"} / {status.source.upstream ?? "—"}</dd></div><div><dt>本地 HEAD</dt><dd><code>{status.local.short_hash ?? status.local.hash ?? "—"}</code></dd></div><div><dt>远端 HEAD</dt><dd><code>{status.remote.short_hash ?? status.remote.hash ?? "—"}</code></dd></div><div><dt>工作区</dt><dd><StateBadge tone={status.dirty ? "error" : "ok"}>{status.dirty ? `有 ${status.dirty_count} 项修改，拒绝更新` : "干净"}</StateBadge></dd></div><div><dt>更新状态</dt><dd>{status.update_available ? `落后 ${status.behind} 个提交` : "已是最新或尚未检查"}</dd></div></dl> : <EmptyState code="plugin_update_status_empty">更新服务没有返回状态。</EmptyState>}
      </QueryBoundary>
    </Panel>
    {section === "update" && <>
      <Panel eyebrow="PLUGIN / BENCHMARK" title="四镜像 + 官方源真实 Git 测速" action={<div className="inline-controls"><button className="button button-secondary" type="button" disabled={benchmark.isPending} onClick={() => benchmark.mutate()}>重新测速</button><button className="button button-primary" type="button" disabled={check.isPending} onClick={() => check.mutate()}>检查更新</button></div>}><UpdateSourceTable operation={operation} /></Panel>
      <Panel eyebrow="PLUGIN / APPLY" title="执行更新"><p>更新前会再次确认工作区干净，并在测速缓存超过 60 秒时重新测速。请输入 <code>UPDATE</code>；只做 fetch 与本地 <code>merge --ff-only</code>，不会自动重启 Bot。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} aria-label="插件更新确认" /><button className="button button-danger" type="button" disabled={confirmation !== "UPDATE" || apply.isPending || status?.dirty === true} onClick={() => apply.mutate()}>执行更新</button></Panel>
    </>}
    {section === "history" && <Panel eyebrow="PLUGIN / HISTORY" title="更新操作历史"><QueryBoundary isPending={historyQuery.isPending} error={historyQuery.error}><BusinessTable rows={history as unknown as BusinessRecord[]} rowKey={(row, index) => textAt(row, "operation_id") + index} emptyCode="plugin_update_history_empty" emptyText="当前进程没有更新操作历史。" columns={[
      { key: "started_at", label: "开始时间", render: (row) => formatDateTime(row.started_at as string | number | null) }, { key: "operation_id", label: "Operation", render: (row) => <code>{textAt(row, "operation_id")}</code> }, { key: "state", label: "结果", render: (row) => <SafeStatus row={row} /> }, { key: "selected_source_id", label: "实际源", render: (row) => textAt(row, "selected_source_id") }, { key: "diagnostic_code", label: "诊断码", render: (row) => <code>{textAt(row, "diagnostic_code")}</code> },
    ]} /></QueryBoundary></Panel>}
    <ResultPanel value={apply.data ?? check.data ?? benchmark.data} />
  </div>;
}
