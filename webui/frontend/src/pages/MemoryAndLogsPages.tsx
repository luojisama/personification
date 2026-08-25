import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { CatalogItem, CursorPage, Page } from "../api/types";
import { asRecord, BusinessTable, recordsAt, SafeStatus, textAt, type BusinessRecord } from "../components/BusinessTable";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { formatDateTime } from "../lib/format";
import { useRuntimeEvents } from "../realtime/RuntimeEventsProvider";

export function MemoryManagementPage() {
  const { section = "recent" } = useParams();
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const recent = useQuery<Page<CatalogItem>>({ queryKey: ["memories", page, search], queryFn: ({ signal }) => resources.catalog("memories", page, 20, search, signal), enabled: section === "recent" });
  const state = useQuery({ queryKey: ["memory-business", section, search], queryFn: ({ signal }) => section === "search" ? resources.memorySearch(search, signal) : resources.memoryBusiness("vector-index", signal), enabled: section !== "recent" && (section !== "search" || Boolean(search.trim())) });
  const rebuild = useMutation({ mutationFn: () => resources.rebuildMemoryIndex() });
  const rows = section === "recent" ? recent.data?.items ?? [] : recordsAt(state.data, "results", "items", "memories");
  return <div className="page-stack">
    <PageHeader index="14" title="Agent 记忆" description="最近记忆、召回测试、内部状态与向量索引分开读取；详情和完整上下文只在选择后加载。" actions={section !== "vector-index" ? <SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder={section === "search" ? "输入召回测试查询" : "搜索安全摘要"} /> : undefined} />
    {section === "vector-index" && <Panel eyebrow="MEMORY / VECTOR" title="向量索引" action={<button className="button button-danger" type="button" disabled={rebuild.isPending} onClick={() => { if (window.confirm("确认后台重建向量索引？当前已知索引会继续提供读取。")) rebuild.mutate(); }}>重建索引</button>}><QueryBoundary isPending={state.isPending} error={state.error}><dl className="detail-list"><div><dt>状态</dt><dd><SafeStatus row={asRecord(state.data)} /></dd></div><div><dt>文档数</dt><dd>{textAt(asRecord(state.data), "document_count", "count", "total")}</dd></div><div><dt>更新时间</dt><dd>{formatDateTime(asRecord(state.data).updated_at as string | number | null)}</dd></div><div><dt>诊断码</dt><dd><code>{textAt(asRecord(state.data), "diagnostic_code", "code")}</code></dd></div></dl></QueryBoundary></Panel>}
    {section !== "vector-index" && <Panel eyebrow={`MEMORY / ${section.toUpperCase()}`} title={section === "search" ? "召回测试结果" : "最近记忆"}>
      <QueryBoundary isPending={recent.isPending || state.isPending} error={recent.error ?? state.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "memory_id", "id") + index} emptyCode={`memory_${section}_empty`} emptyText={section === "search" && !search ? "输入查询后执行召回测试。" : "当前没有匹配的记忆记录。"} columns={[
          { key: "summary", label: "记忆摘要", render: (row) => <><strong>{textAt(row, "summary", "content_summary")}</strong><br /><code>{textAt(row, "memory_id", "id")}</code></> },
          { key: "scope", label: "作用域", render: (row) => textAt(row, "scope", "session_type", "group_id") },
          { key: "source", label: "来源", render: (row) => textAt(row, "source_kind", "source") },
          { key: "status", label: "状态", render: (row) => <SafeStatus row={row} /> },
          { key: "expires_at", label: "过期", render: (row) => formatDateTime(row.expires_at as string | number | null) },
        ]} />
      </QueryBoundary>
      {recent.data && <Pagination page={recent.data.page} totalPages={recent.data.total_pages} onChange={setPage} />}
    </Panel>}
    {rebuild.data && <DiagnosticPanel diagnostic={safeDiagnostic(asRecord(asRecord(rebuild.data).diagnostic).code ? asRecord(asRecord(rebuild.data).diagnostic) : asRecord(rebuild.data))} defaultOpen />}
  </div>;
}

export function PluginLogsPage() {
  const { section = "live" } = useParams();
  const realtime = useRuntimeEvents();
  const [search, setSearch] = useState("");
  const [cursorStack, setCursorStack] = useState([0]);
  const [confirmation, setConfirmation] = useState("");
  const cursor = cursorStack[cursorStack.length - 1] ?? 0;
  const history = useQuery<CursorPage<CatalogItem>>({ queryKey: ["logs", cursor, search], queryFn: ({ signal }) => resources.logs(100, cursor, search, signal), enabled: section !== "live" });
  const clear = useMutation({ mutationFn: () => resources.clearLogs() });
  const liveRows = realtime.events.filter((event) => event.topic === "log.appended").map((event) => ({ ...event.payload, id: event.id, ts: event.ts, topic: event.topic } as BusinessRecord)).slice(-100).reverse();
  const rows = section === "live" ? liveRows : history.data?.items ?? [];
  return <div className="page-stack">
    <PageHeader index="29" title="插件日志" description="实时 SSE、历史游标搜索、Trace 过滤和明确确认的清理操作分开呈现；事件 payload 已由服务端脱敏。" actions={section !== "cleanup" ? <SearchField value={search} onChange={(value) => { setSearch(value); setCursorStack([0]); }} placeholder="搜索级别、来源、Trace 或安全摘要" /> : undefined} />
    {section !== "cleanup" && <Panel eyebrow={`LOGS / ${section.toUpperCase()}`} title={section === "live" ? `实时流 · SSE ${realtime.state}` : "历史日志"}>
      <QueryBoundary isPending={history.isPending && section !== "live"} error={history.error}>
        <BusinessTable rows={rows} rowKey={(row, index) => textAt(row, "id", "ts") + index} emptyCode={`logs_${section}_empty`} emptyText={section === "live" ? "当前 SSE 窗口没有日志事件。" : "当前筛选没有历史日志。"} columns={[
          { key: "ts", label: "时间", render: (row) => formatDateTime(row.ts as string | number | null) },
          { key: "level", label: "级别", render: (row) => <SafeStatus row={row} keys={["level", "status"]} /> },
          { key: "source", label: "来源", render: (row) => textAt(row, "source", "logger", "topic") },
          { key: "message", label: "安全摘要", render: (row) => textAt(row, "message", "summary", "code") },
          { key: "trace_id", label: "Trace", render: (row) => <code>{textAt(row, "trace_id")}</code> },
        ]} />
      </QueryBoundary>
      {history.data && section === "history" && <div className="pagination"><button type="button" disabled={cursorStack.length <= 1} onClick={() => setCursorStack((values) => values.slice(0, -1))}>较新</button><span>游标页 {cursorStack.length}</span><button type="button" disabled={!history.data.has_more || !history.data.next_cursor} onClick={() => setCursorStack((values) => [...values, history.data?.next_cursor ?? 0])}>较早</button></div>}
    </Panel>}
    {section === "cleanup" && <Panel eyebrow="LOGS / CLEANUP" title="清理插件日志"><p>该操作只清理插件管理日志，不影响 Trace 数据库。输入 <code>CLEAR LOGS</code> 才能提交。</p><input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button className="button button-danger" type="button" disabled={confirmation !== "CLEAR LOGS" || clear.isPending} onClick={() => clear.mutate()}>清理日志</button></Panel>}
    {clear.data && <DiagnosticPanel diagnostic={safeDiagnostic(asRecord(clear.data))} defaultOpen />}
  </div>;
}
