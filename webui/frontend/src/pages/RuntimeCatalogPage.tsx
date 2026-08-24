import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { resources } from "../api/resources";
import type { CatalogItem, CursorPage, Page } from "../api/types";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime } from "../lib/format";

type PagedDataset = "plugin-knowledge" | "mcp" | "skills" | "tool-tasks" | "memories";
type Dataset = PagedDataset | "logs";

const DATASETS: Array<{ value: Dataset; label: string }> = [
  { value: "plugin-knowledge", label: "插件知识" },
  { value: "mcp", label: "MCP" },
  { value: "skills", label: "Skill / 工具" },
  { value: "tool-tasks", label: "工具创建任务" },
  { value: "memories", label: "长期记忆" },
  { value: "logs", label: "运行日志" },
];

const TITLE_KEYS: Record<Dataset, string[]> = {
  "plugin-knowledge": ["display_name", "plugin_name"],
  mcp: ["name", "server_name", "installation_id"],
  skills: ["name"],
  "tool-tasks": ["suggested_name", "task_id"],
  memories: ["summary", "memory_id"],
  logs: ["message", "source"],
};

const DETAIL_KEYS: Record<Dataset, string[]> = {
  "plugin-knowledge": ["summary", "analysis_scope", "category"],
  mcp: ["description", "source_id", "command"],
  skills: ["description", "category", "source_kind"],
  "tool-tasks": ["request_text", "phase", "error"],
  memories: ["memory_type", "source_kind", "palace_zone"],
  logs: ["source", "trace_id", "level"],
};

function text(item: CatalogItem, keys: string[], fallback: string): string {
  for (const key of keys) {
    const value = item[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return fallback;
}

function timestamp(item: CatalogItem): string {
  for (const key of ["updated_at", "created_at", "ts"]) {
    const value = item[key];
    if ((typeof value === "string" && value) || typeof value === "number") return formatDateTime(value);
  }
  return "—";
}

function status(item: CatalogItem): string {
  for (const key of ["status", "phase", "level", "source_kind", "category"]) {
    const value = item[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  if (item.enabled_by_config === true && item.user_disabled !== true) return "enabled";
  if (item.enabled_by_config === false || item.user_disabled === true) return "disabled";
  return "record";
}

function rowKey(item: CatalogItem, index: number): string {
  return text(item, ["plugin_name", "installation_id", "name", "task_id", "memory_id", "id"], String(index));
}

function isCursorPage(value: Page<CatalogItem> | CursorPage<CatalogItem>): value is CursorPage<CatalogItem> {
  return "has_more" in value;
}

export function RuntimeCatalogPage() {
  const [dataset, setDataset] = useState<Dataset>("plugin-knowledge");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [logCursors, setLogCursors] = useState([0]);
  const logCursor = logCursors[logCursors.length - 1] ?? 0;
  const query = useQuery<Page<CatalogItem> | CursorPage<CatalogItem>>({
    queryKey: ["runtime-catalog", dataset, page, search, logCursor],
    queryFn: ({ signal }) => dataset === "logs"
      ? resources.logs(100, logCursor, search, signal)
      : resources.catalog(dataset, page, 20, search, signal),
  });
  const rows = query.data?.items ?? [];
  const cursorPage = query.data && isCursorPage(query.data) ? query.data : null;
  const pagedData = query.data && !isCursorPage(query.data) ? query.data : null;
  const title = useMemo(() => DATASETS.find((item) => item.value === dataset)?.label ?? "运行目录", [dataset]);

  const selectDataset = (value: Dataset) => {
    setDataset(value);
    setPage(1);
    setSearch("");
    setLogCursors([0]);
  };

  return (
    <div className="page-stack">
      <PageHeader
        index="06"
        title="运行目录"
        description="统一查看插件知识、MCP、Skill、工具任务、记忆与日志。增长型日志使用游标翻页，其余目录使用服务端页码分页。"
        actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); setLogCursors([0]); }} placeholder="搜索当前目录" />}
      />
      <div className="data-tabs" role="tablist" aria-label="运行目录数据集">
        {DATASETS.map((item) => (
          <button key={item.value} type="button" role="tab" aria-selected={dataset === item.value} onClick={() => selectDataset(item.value)}>
            {item.label}
          </button>
        ))}
      </div>
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {rows.length === 0 ? (
          <EmptyState code={`${dataset}_catalog_empty`}>当前筛选条件下没有记录。</EmptyState>
        ) : (
          <Panel eyebrow={`CATALOG / ${dataset.toUpperCase()}`} title={title}>
            <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>条目</th><th>安全摘要</th><th>状态</th><th>更新时间</th></tr></thead><tbody>
              {rows.map((item, index) => (
                <tr key={rowKey(item, index)}>
                  <td><strong>{text(item, TITLE_KEYS[dataset], "未命名条目")}</strong><br /><code>{rowKey(item, index)}</code></td>
                  <td className="wrap-cell">{text(item, DETAIL_KEYS[dataset], "暂无可见摘要")}</td>
                  <td><StateBadge tone="info" raw={status(item)}>{status(item)}</StateBadge></td>
                  <td>{timestamp(item)}</td>
                </tr>
              ))}
            </tbody></table></div>
          </Panel>
        )}
      </QueryBoundary>
      {pagedData && <Pagination page={pagedData.page} totalPages={pagedData.total_pages} onChange={setPage} />}
      {cursorPage && (
        <div className="pagination" aria-label="日志游标分页">
          <button type="button" disabled={logCursors.length <= 1} onClick={() => setLogCursors((values) => values.slice(0, -1))}>较新日志</button>
          <span>游标页 {logCursors.length}</span>
          <button
            type="button"
            disabled={!cursorPage.has_more || !cursorPage.next_cursor}
            onClick={() => setLogCursors((values) => [...values, cursorPage.next_cursor])}
          >较早日志</button>
        </div>
      )}
    </div>
  );
}
