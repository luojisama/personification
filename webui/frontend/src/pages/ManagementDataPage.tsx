import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { ConfigListItem, GroupListItem, Page, PersonaListItem, StickerListItem } from "../api/types";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime, formatInteger } from "../lib/format";

type Dataset = "personas" | "groups" | "stickers" | "config";
type ManagementRow = PersonaListItem | GroupListItem | StickerListItem | ConfigListItem;
type ManagementPage = Page<ManagementRow> & Partial<{
  index_status: string;
  index_detail_code: string;
  index_updated_at: number;
  index_stale: boolean;
}>;

const DATASETS: Array<{ value: Dataset; label: string }> = [
  { value: "personas", label: "用户画像" },
  { value: "groups", label: "群目录" },
  { value: "stickers", label: "贴纸索引" },
  { value: "config", label: "配置注册表" },
];

export function ManagementDataPage() {
  const [dataset, setDataset] = useState<Dataset>("personas");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const query = useQuery<ManagementPage>({
    queryKey: ["management-data", dataset, page, search],
    queryFn: async ({ signal }) => {
      if (dataset === "personas") return await resources.personas(page, 20, search, signal) as ManagementPage;
      if (dataset === "groups") return await resources.groups(page, 20, search, signal) as ManagementPage;
      if (dataset === "stickers") return await resources.stickers(page, 20, search, signal) as ManagementPage;
      return await resources.config(page, 20, search, signal) as ManagementPage;
    },
  });
  const selectDataset = (value: Dataset) => {
    setDataset(value);
    setPage(1);
    setSearch("");
  };

  return (
    <div className="page-stack">
      <PageHeader
        index="05"
        title="管理数据"
        description="统一分页读取画像、群、贴纸和配置。首屏只使用本地缓存；QQ 资料刷新与贴纸目录扫描不会阻塞列表请求。"
        actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="搜索当前数据集" />}
      />
      <div className="data-tabs" role="tablist" aria-label="管理数据集">
        {DATASETS.map((item) => (
          <button key={item.value} type="button" role="tab" aria-selected={dataset === item.value} onClick={() => selectDataset(item.value)}>
            {item.label}
          </button>
        ))}
      </div>
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {query.data && query.data.items.length === 0 ? (
          <EmptyState code={`${dataset}_list_empty`}>当前筛选条件下没有记录。</EmptyState>
        ) : (
          <>
            {dataset === "personas" && <PersonaTable rows={(query.data?.items ?? []) as PersonaListItem[]} />}
            {dataset === "groups" && <GroupTable rows={(query.data?.items ?? []) as GroupListItem[]} />}
            {dataset === "stickers" && <StickerTable rows={(query.data?.items ?? []) as StickerListItem[]} meta={query.data} />}
            {dataset === "config" && <ConfigTable rows={(query.data?.items ?? []) as ConfigListItem[]} />}
          </>
        )}
      </QueryBoundary>
      {query.data && <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={setPage} />}
    </div>
  );
}

function PersonaTable({ rows }: { rows: PersonaListItem[] }) {
  return (
    <Panel eyebrow="CACHE / PERSONAS" title="画像摘要索引">
      <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>用户</th><th>画像摘要</th><th>好感</th><th>更新时间</th><th>来源</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.user_id}><td><strong>{item.nickname || item.user_id}</strong><br /><code>{item.user_id}</code></td><td className="wrap-cell">{item.snippet || "暂无可见摘要"}</td><td>{item.favorability.level || "未分级"} · {item.favorability.score}</td><td>{formatDateTime(item.updated_at)}</td><td><StateBadge tone="info">缓存</StateBadge> {item.source}</td></tr>)}
      </tbody></table></div>
    </Panel>
  );
}

function GroupTable({ rows }: { rows: GroupListItem[] }) {
  return (
    <Panel eyebrow="CACHE / GROUPS" title="群目录快照">
      <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>群</th><th>开关</th><th>已确认 Bot</th><th>来源</th><th>最近观察</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.group_id}><td><strong>{item.group_name || "未缓存群名"}</strong><br /><code>{item.group_id}</code></td><td><StateBadge tone={item.enabled ? "ok" : "unknown"}>{item.enabled ? "启用" : "停用"}</StateBadge></td><td>{item.bot_self_ids.join("、") || "未确认"}</td><td className="wrap-cell">{item.sources.join("、")}</td><td>{formatDateTime(item.freshness)}</td></tr>)}
      </tbody></table></div>
    </Panel>
  );
}

function StickerTable({ rows, meta }: { rows: StickerListItem[]; meta?: ManagementPage }) {
  const queryClient = useQueryClient();
  const history = useDiagnosticHistory("sticker-index");
  const rebuild = useMutation({
    mutationFn: () => resources.rebuildStickerIndex(),
    onSuccess: (diagnostic) => { history.record(diagnostic); void queryClient.invalidateQueries({ queryKey: ["management-data", "stickers"] }); },
    onError: (error) => history.record(diagnosticFromError(error)),
  });
  return (
    <div className="page-stack compact-stack">
      <Panel eyebrow="INDEX / STICKERS" title="持久贴纸索引" action={<button className="button button-secondary" type="button" disabled={rebuild.isPending} onClick={() => rebuild.mutate()}>{rebuild.isPending ? "已排队" : "后台重建索引"}</button>}>
        <div className="index-status-line"><StateBadge tone={meta?.index_stale ? "warn" : "ok"}>{meta?.index_stale ? "索引待刷新" : "索引当前"}</StateBadge><code>{meta?.index_detail_code}</code><span>更新时间 {formatDateTime(meta?.index_updated_at)}</span></div>
        <div className="sticker-grid">{rows.map((item) => <article key={item.filename}><img src={item.thumbnail_url} alt="" loading="lazy" /><div><strong>{item.filename}</strong><p>{item.description || "未标注"}</p><small>{formatInteger(item.size_bytes)} B · {[...item.mood_tags, ...item.scene_tags].join(" / ") || "无标签"}</small></div></article>)}</div>
      </Panel>
      {history.diagnostics.map((diagnostic, index) => <DiagnosticPanel key={`${diagnostic.code}:${index}`} diagnostic={diagnostic} defaultOpen={index === 0} />)}
    </div>
  );
}

function ConfigTable({ rows }: { rows: ConfigListItem[] }) {
  return (
    <Panel eyebrow="REGISTRY / CONFIG" title="配置注册表安全视图">
      <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>配置</th><th>当前值</th><th>类型</th><th>分组</th><th>生效方式</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.field_name}><td><strong>{item.display_name}</strong><br /><code>{item.field_name}</code></td><td className="wrap-cell"><code>{typeof item.value === "string" ? item.value : JSON.stringify(item.value)}</code></td><td>{item.value_type}{item.secret && <StateBadge tone="warn">秘密已掩码</StateBadge>}</td><td>{item.group}</td><td>{item.hot_reloadable ? "热更新" : "需重启"}</td></tr>)}
      </tbody></table></div>
    </Panel>
  );
}
