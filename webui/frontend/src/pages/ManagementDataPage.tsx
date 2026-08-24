import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import { useBot } from "../app/BotContext";
import type { GroupListItem, Page, PersonaListItem, StickerListItem } from "../api/types";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { IdentityAvatar } from "../components/IdentityAvatar";
import { formatDateTime, formatInteger } from "../lib/format";

type Dataset = "personas" | "groups" | "stickers";
type ManagementRow = PersonaListItem | GroupListItem | StickerListItem;
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
];

export function ManagementDataPage({ dataset: fixedDataset }: { dataset?: Dataset } = {}) {
  const { botId } = useBot();
  const [selectedDataset, setSelectedDataset] = useState<Dataset>(fixedDataset ?? "personas");
  const dataset = fixedDataset ?? selectedDataset;
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [groupId, setGroupId] = useState("");
  const [favorabilityLevel, setFavorabilityLevel] = useState("");
  const [sortBy, setSortBy] = useState("updated_at");
  const [membershipState, setMembershipState] = useState("");
  const [includeUnconfirmed, setIncludeUnconfirmed] = useState(false);
  const [enabled, setEnabled] = useState("");
  const query = useQuery<ManagementPage>({
    queryKey: ["management-data", dataset, page, search, groupId, favorabilityLevel, sortBy, membershipState, includeUnconfirmed, enabled, botId],
    queryFn: async ({ signal }) => {
      if (dataset === "personas") return await resources.personasFiltered(page, 20, { search, group_id: groupId, favorability_level: favorabilityLevel, sort_by: sortBy, direction: sortBy === "user_id" ? "asc" : "desc" }, signal) as ManagementPage;
      if (dataset === "groups") return await resources.groupsFiltered(page, 20, { search, membership_state: membershipState, include_unconfirmed: includeUnconfirmed, enabled, bot_id: botId, sort_by: sortBy === "updated_at" ? "group_id" : sortBy, direction: "asc" }, signal) as ManagementPage;
      return await resources.stickers(page, 20, search, signal) as ManagementPage;
    },
  });
  const selectDataset = (value: Dataset) => {
    setSelectedDataset(value);
    setPage(1);
    setSearch("");
  };

  return (
    <div className="page-stack">
      <PageHeader
        index={dataset === "personas" ? "11" : dataset === "groups" ? "12" : "16"}
        title={dataset === "personas" ? "用户画像" : dataset === "groups" ? "群信息" : "表情包"}
        description={dataset === "personas" ? "头像、昵称、QQ ID 与好感度摘要来自本地缓存；列表不会逐行调用 OneBot，也不会暴露原始画像正文。" : dataset === "groups" ? "展示群头像、群 ID、关联 Bot 与成员关系来源；默认不把历史画像候选伪装成当前已加入群。" : "读取持久化贴纸索引，支持分页、标签与后台增量重建。"}
        actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="搜索当前数据集" />}
      />
      {!fixedDataset && <div className="data-tabs" role="tablist" aria-label="管理数据集">
        {DATASETS.map((item) => (
          <button key={item.value} type="button" role="tab" aria-selected={dataset === item.value} onClick={() => selectDataset(item.value)}>
            {item.label}
          </button>
        ))}
      </div>}
      {dataset === "personas" && <Panel eyebrow="FILTER / PERSONAS" title="画像筛选"><div className="inline-controls filter-control-row"><input value={groupId} onChange={(event) => { setGroupId(event.target.value); setPage(1); }} placeholder="群 ID" aria-label="按群筛选" /><input value={favorabilityLevel} onChange={(event) => { setFavorabilityLevel(event.target.value); setPage(1); }} placeholder="好感等级" aria-label="按好感等级筛选" /><select value={sortBy} onChange={(event) => { setSortBy(event.target.value); setPage(1); }} aria-label="画像排序"><option value="updated_at">更新时间降序</option><option value="favorability">好感度降序</option><option value="user_id">QQ 号升序</option></select></div></Panel>}
      {dataset === "groups" && <Panel eyebrow="FILTER / GROUPS" title="群目录筛选"><div className="inline-controls filter-control-row"><select value={membershipState} onChange={(event) => { setMembershipState(event.target.value); if (event.target.value === "unconfirmed") setIncludeUnconfirmed(true); setPage(1); }} aria-label="关系来源"><option value="">确认与配置</option><option value="confirmed">仅已确认</option><option value="configured">仅配置</option><option value="unconfirmed">仅未确认候选</option></select><select value={enabled} onChange={(event) => { setEnabled(event.target.value); setPage(1); }} aria-label="群开关状态"><option value="">全部开关</option><option value="true">已启用</option><option value="false">已停用</option></select><label className="checkbox-label"><input type="checkbox" checked={includeUnconfirmed} onChange={(event) => { setIncludeUnconfirmed(event.target.checked); setPage(1); }} />显示未确认候选</label></div></Panel>}
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {query.data && query.data.items.length === 0 ? (
          <EmptyState code={`${dataset}_list_empty`}>当前筛选条件下没有记录。</EmptyState>
        ) : (
          <>
            {dataset === "personas" && <PersonaTable rows={(query.data?.items ?? []) as PersonaListItem[]} />}
            {dataset === "groups" && <GroupTable rows={(query.data?.items ?? []) as GroupListItem[]} />}
            {dataset === "stickers" && <StickerTable rows={(query.data?.items ?? []) as StickerListItem[]} meta={query.data} />}
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
      <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>用户</th><th>QQ ID</th><th>好感</th><th>最近群</th><th>更新时间</th><th>来源</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.user_id}><td><div className="table-identity"><IdentityAvatar src={item.avatar_url} label={item.nickname || item.user_id} /><strong>{item.nickname || "未缓存昵称"}</strong></div></td><td><code>{item.qq_id || item.user_id}</code><button className="copy-id" type="button" onClick={() => void navigator.clipboard.writeText(item.qq_id || item.user_id)}>复制</button></td><td>{item.favorability_level || item.favorability.level || "未分级"} · {item.favorability_score ?? item.favorability.score}</td><td><code>{item.recent_group_id || "—"}</code></td><td>{formatDateTime(item.updated_at)}</td><td><StateBadge tone="info">缓存</StateBadge> {item.source}</td></tr>)}
      </tbody></table></div>
    </Panel>
  );
}

function GroupTable({ rows }: { rows: GroupListItem[] }) {
  return (
    <Panel eyebrow="CACHE / GROUPS" title="群目录快照">
      <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>群</th><th>群 ID</th><th>关系</th><th>开关</th><th>关联 Bot</th><th>成员</th><th>最近观察</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.group_id}><td><div className="table-identity"><IdentityAvatar src={item.avatar_url} label={item.group_name || item.group_id} /><strong>{item.group_name || "未缓存群名"}</strong></div></td><td><code>{item.group_id}</code></td><td><StateBadge tone={item.membership_state === "confirmed" ? "ok" : item.membership_state === "configured" ? "info" : "warn"} raw={item.membership_state}>{item.membership_state === "confirmed" ? "已确认" : item.membership_state === "configured" ? "仅配置" : "未确认候选"}</StateBadge></td><td><StateBadge tone={item.enabled ? "ok" : "unknown"}>{item.enabled ? "启用" : "停用"}</StateBadge></td><td>{(item.bot_ids || item.bot_self_ids).join("、") || "未确认"}</td><td>{item.member_count ?? "—"}</td><td>{formatDateTime(item.last_active_at ?? item.freshness)}</td></tr>)}
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
