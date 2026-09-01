import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { diagnosticFromError, safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { GroupListItem, Page, PersonaListItem, StickerListItem } from "../api/types";
import { useBot } from "../app/BotContext";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { IdentityAvatar } from "../components/IdentityAvatar";
import { GroupPeerBotsPanel } from "../components/GroupPeerBotsPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime, formatInteger } from "../lib/format";

type Dataset = "personas" | "groups" | "stickers";
type ManagementRow = PersonaListItem | GroupListItem | StickerListItem;
type JsonRecord = Record<string, unknown>;
type ManagementPage = Page<ManagementRow> & Partial<{
  index_status: string;
  index_detail_code: string;
  index_updated_at: number;
  index_stale: boolean;
  index: { state: string; detail_code: string; indexed_at: number; counts: Record<string, number> };
}>;

function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {};
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function text(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) return value.map((item) => String(item)).join("、") || fallback;
  if (typeof value === "object") return fallback;
  return String(value);
}

function resultDiagnostic(result: unknown) {
  const payload = record(result);
  const source = Object.keys(record(payload.diagnostic)).length ? record(payload.diagnostic) : payload;
  return safeDiagnostic(source as Parameters<typeof safeDiagnostic>[0]);
}

function useUrlState() {
  const [params, setParams] = useSearchParams();
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "page") next.set("page", "1");
    setParams(next);
  };
  return { params, set };
}

function useDebouncedSearch(value: string, apply: (value: string) => void) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  useEffect(() => {
    if (draft === value) return;
    const timer = window.setTimeout(() => apply(draft), 300);
    return () => window.clearTimeout(timer);
  }, [apply, draft, value]);
  return { draft, setDraft };
}

export function PersonasPage() { return <ManagementDataView dataset="personas" />; }
export function GroupsPage() { return <ManagementDataView dataset="groups" />; }
export function StickersPage() { return <ManagementDataView dataset="stickers" />; }

function ManagementDataView({ dataset }: { dataset: Dataset }) {
  const { section = dataset === "stickers" ? "catalog" : "list" } = useParams();
  if (dataset === "personas" && section !== "list") return <PersonaDetail section={section} />;
  if (dataset === "groups" && section !== "list") return <GroupDetail section={section} />;
  if (dataset === "stickers" && section !== "catalog") return <StickerOperations section={section} />;
  return <ManagementList dataset={dataset} />;
}

function ManagementList({ dataset }: { dataset: Dataset }) {
  const { botId } = useBot();
  const navigate = useNavigate();
  const { params, set } = useUrlState();
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const search = params.get("search") ?? "";
  const groupId = params.get("group_id") ?? "";
  const favorabilityLevel = params.get("favorability_level") ?? "";
  const sortBy = params.get("sort") ?? "updated_at";
  const membershipState = params.get("membership_state") ?? "";
  const includeUnconfirmed = params.get("include_unconfirmed") === "true";
  const enabled = params.get("enabled") ?? "";
  const searchInput = useDebouncedSearch(search, (value) => set("search", value));
  const query = useQuery<ManagementPage>({
    queryKey: ["management-data", dataset, page, search, groupId, favorabilityLevel, sortBy, membershipState, includeUnconfirmed, enabled, botId],
    queryFn: async ({ signal }) => {
      if (dataset === "personas") return await resources.personasFiltered(page, 20, { search, group_id: groupId, favorability_level: favorabilityLevel, sort_by: sortBy, direction: sortBy === "user_id" ? "asc" : "desc" }, signal) as ManagementPage;
      if (dataset === "groups") return await resources.groupsFiltered(page, 20, { search, membership_state: membershipState, include_unconfirmed: includeUnconfirmed, enabled, bot_id: botId, sort_by: sortBy === "updated_at" ? "group_id" : sortBy, direction: "asc" }, signal) as ManagementPage;
      return await resources.stickers(page, 20, search, signal) as ManagementPage;
    },
    placeholderData: (previous) => previous,
  });
  const openDetail = (id: string) => {
    const target = dataset === "personas" ? "/persona/personas/detail" : dataset === "groups" ? "/persona/groups/detail" : "/persona/stickers/upload";
    const key = dataset === "personas" ? "user_id" : dataset === "groups" ? "group_id" : "sticker";
    navigate(`${target}?${key}=${encodeURIComponent(id)}`);
  };
  return <div className="page-stack">
    <PageHeader index={dataset === "personas" ? "用户画像 / 列表" : dataset === "groups" ? "群信息 / 列表" : "表情包 / 目录"} title={dataset === "personas" ? "用户画像" : dataset === "groups" ? "群信息" : "表情包"} description={dataset === "personas" ? "只读取白名单摘要投影；不逐行调用 OneBot，也不返回原始画像正文。" : dataset === "groups" ? "群目录使用 SQL 分页；未确认历史候选默认隐藏。" : "读取持久化贴纸索引；完整 metadata 只在编辑时加载当前项。"} actions={<SearchField value={searchInput.draft} onChange={searchInput.setDraft} placeholder="搜索当前目录" />} />
    {dataset === "personas" && <Panel eyebrow="FILTER / PERSONAS" title="服务端筛选"><div className="inline-controls filter-control-row"><input value={groupId} onChange={(event) => set("group_id", event.target.value)} placeholder="群 ID" aria-label="按群筛选" /><input value={favorabilityLevel} onChange={(event) => set("favorability_level", event.target.value)} placeholder="好感等级" aria-label="按好感等级筛选" /><select value={sortBy} onChange={(event) => set("sort", event.target.value)} aria-label="画像排序"><option value="updated_at">更新时间降序</option><option value="favorability">好感度降序</option><option value="user_id">QQ 号升序</option></select></div></Panel>}
    {dataset === "groups" && <Panel eyebrow="FILTER / GROUPS" title="服务端筛选"><div className="inline-controls filter-control-row"><select value={membershipState} onChange={(event) => { set("membership_state", event.target.value); if (event.target.value === "unconfirmed") set("include_unconfirmed", "true"); }} aria-label="关系来源"><option value="">确认与配置</option><option value="confirmed">仅已确认</option><option value="configured">仅配置</option><option value="unconfirmed">仅未确认候选</option></select><select value={enabled} onChange={(event) => set("enabled", event.target.value)} aria-label="群开关状态"><option value="">全部开关</option><option value="true">已启用</option><option value="false">已停用</option></select><label className="checkbox-label"><input type="checkbox" checked={includeUnconfirmed} onChange={(event) => set("include_unconfirmed", event.target.checked ? "true" : "")} />显示未确认候选</label></div></Panel>}
    <QueryBoundary isPending={query.isPending} error={query.error}>{query.data?.items.length === 0 ? <EmptyState code={`${dataset}_list_empty`}>当前筛选条件下没有记录。</EmptyState> : <>{dataset === "personas" && <PersonaTable rows={(query.data?.items ?? []) as PersonaListItem[]} onOpen={openDetail} />}{dataset === "groups" && <GroupTable rows={(query.data?.items ?? []) as GroupListItem[]} onOpen={openDetail} />}{dataset === "stickers" && <StickerCatalog rows={(query.data?.items ?? []) as StickerListItem[]} onOpen={openDetail} />}</>}</QueryBoundary>
    {query.data?.index && <div className="index-status-line"><StateBadge tone={query.data.index.state === "ready" ? "ok" : "running"}>{query.data.index.state === "ready" ? "管理投影已就绪" : "管理投影后台重建中"}</StateBadge><code>{query.data.index.detail_code}</code><span>索引时间 {formatDateTime(query.data.index.indexed_at)}</span></div>}
    {query.data && <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={(value) => set("page", String(value))} />}
  </div>;
}

function PersonaTable({ rows, onOpen }: { rows: PersonaListItem[]; onOpen: (id: string) => void }) {
  return <Panel eyebrow="CACHE / PERSONAS" title="画像摘要索引"><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>用户</th><th>QQ ID</th><th>好感</th><th>最近群</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{rows.map((item) => { const score = Number(item.favorability_score ?? item.favorability.score ?? 0); return <tr key={item.user_id}><td><div className="table-identity"><IdentityAvatar src={item.avatar_url} label={item.nickname || item.user_id} /><strong>{item.nickname || "未缓存昵称"}</strong></div></td><td><code>{item.qq_id || item.user_id}</code><button className="copy-id" type="button" onClick={() => void navigator.clipboard.writeText(item.qq_id || item.user_id)}>复制</button></td><td className={score < 0 ? "state-error" : ""}>{item.favorability_level || item.favorability.level || "未分级"} · {item.favorability_score ?? item.favorability.score}</td><td><code>{item.recent_group_id || "—"}</code></td><td>{formatDateTime(item.updated_at)}</td><td><button className="button button-secondary" type="button" onClick={() => onOpen(item.user_id)}>查看画像</button></td></tr>; })}</tbody></table></div></Panel>;
}

export function favorabilityDisplayClass(score: unknown): string {
  return Number(score) < 0 ? "state-error" : "";
}

export function GroupMemberFavorabilityCell({ member }: { member: JsonRecord }) {
  const favorability = record(member.favorability);
  const score = favorability.score ?? member.favorability_score ?? 0;
  const level = text(favorability.level ?? member.favorability_level, "未分级");
  return <td className={favorabilityDisplayClass(score)}>{level} · {text(score, "0")}</td>;
}

export function GroupMembersPanel({ profiles }: { profiles: JsonRecord[] }) {
  return <Panel eyebrow="GROUP / MEMBERS" title={`成员画像（${profiles.length}）`}><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>成员</th><th>昵称</th><th>好感</th><th>关系</th></tr></thead><tbody>{profiles.map((item, index) => <tr key={text(item.user_id, String(index))}><td><code>{text(item.user_id)}</code></td><td>{text(item.nickname ?? item.card)}</td><GroupMemberFavorabilityCell member={item} /><td>{text(item.relationship)}</td></tr>)}</tbody></table></div></Panel>;
}

export function GroupAliasesPanel({ aliases, memberId, aliasText, pending, onMemberIdChange, onAliasTextChange, onSave, onDelete }: { aliases: JsonRecord[]; memberId: string; aliasText: string; pending: boolean; onMemberIdChange: (value: string) => void; onAliasTextChange: (value: string) => void; onSave: () => void; onDelete: () => void }) {
  return <Panel eyebrow="GROUP / ALIASES" title={`成员别名（${aliases.length}）`}><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>成员 ID</th><th>称呼</th><th>备注</th></tr></thead><tbody>{aliases.map((item, index) => <tr key={text(item.user_id, String(index))}><td><code>{text(item.user_id)}</code></td><td>{text(item.aliases)}</td><td>{text(item.note)}</td></tr>)}</tbody></table></div><div className="form-grid"><label>成员 QQ<input value={memberId} onChange={(event) => onMemberIdChange(event.target.value)} /></label><label>称呼（逗号分隔）<input value={aliasText} onChange={(event) => onAliasTextChange(event.target.value)} /></label></div><div className="inline-controls"><button className="button" type="button" disabled={!memberId || !aliasText || pending} onClick={onSave}>保存别名</button><button className="button button-danger" type="button" disabled={!memberId || pending} onClick={onDelete}>删除别名</button></div></Panel>;
}

export function GroupFavorabilityPanel({ favorability, eyebrow = "GROUP / FAVORABILITY" }: { favorability: JsonRecord; eyebrow?: string }) {
  const policy = record(favorability.behavior_policy);
  const score = Number(favorability.score ?? 0);
  const signed = (value: unknown) => `${Number(value ?? 0) >= 0 ? "+" : ""}${text(value, "0")}`;
  return <Panel eyebrow={eyebrow} title={text(favorability.level, "未分级")}><dl className="compact-kv"><dt>分数</dt><dd className={favorabilityDisplayClass(score)}>{text(favorability.score, "0")} / {text(favorability.score_min, "-100")}..{text(favorability.score_max, "100")}</dd><dt>今日加分</dt><dd>{signed(favorability.daily_positive_count)}</dd><dt>今日扣分</dt><dd>-{text(favorability.daily_negative_count, "0")}</dd><dt>今日净变化</dt><dd>{signed(favorability.daily_net_count)}</dd><dt>群随机偏置</dt><dd>{signed(policy.random_reply_add)}</dd><dt>群闲偏置</dt><dd>{signed(policy.group_idle_add)}</dd></dl></Panel>;
}

function PersonaDetail({ section }: { section: string }) {
  const { botId } = useBot();
  const { params, set } = useUrlState();
  const userId = params.get("user_id") ?? "";
  const groupId = params.get("group_id") ?? "";
  const [field, setField] = useState("");
  const [value, setValue] = useState("");
  const history = useDiagnosticHistory("persona-detail");
  const query = useQuery({ queryKey: ["persona-detail", userId, groupId], queryFn: ({ signal }) => resources.personaDetail(userId, groupId, signal), enabled: Boolean(userId) });
  const run = useMutation({ mutationFn: async (action: "refresh" | "correct" | "avatar" | "clear-avatar") => action === "refresh" ? resources.refreshPersona(userId, groupId, botId) : action === "correct" ? resources.correctPersona(userId, { corrections: { [field]: value } }) : action === "avatar" ? resources.refreshPersonaAvatar(userId) : resources.clearPersonaAvatar(userId), onSuccess: (result) => { history.record(resultDiagnostic(result)); void query.refetch(); }, onError: (error) => history.record(diagnosticFromError(error)) });
  const core = record(query.data?.core_profile);
  const qq = record(core.qq_profile);
  const favorability = record(query.data?.favorability);
  const structured = record(core.structured);
  return <div className="page-stack">
    <PageHeader index={`用户画像 / ${section === "refresh" ? "后台刷新" : "详情"}`} title="画像详情" description="详情按 QQ ID 懒加载，并把 QQ 公开资料、用户明确更正、系统观察和模型结构化字段分区展示。" actions={<div className="inline-controls"><input value={userId} onChange={(event) => set("user_id", event.target.value)} placeholder="QQ ID" inputMode="numeric" /><input value={groupId} onChange={(event) => set("group_id", event.target.value)} placeholder="群 ID（可选）" inputMode="numeric" /></div>} />
    {!userId ? <EmptyState code="persona_id_required">从画像列表选择用户，或输入 QQ ID。</EmptyState> : <QueryBoundary isPending={query.isPending} error={query.error}>{query.data && <>
      <div className="summary-grid"><Panel eyebrow="QQ / PUBLIC PROFILE" title={text(qq.nickname, userId)}><dl className="compact-kv"><dt>QQ ID</dt><dd><code>{userId}</code></dd><dt>签名</dt><dd>{text(qq.signature)}</dd><dt>所在地</dt><dd>{[qq.country, qq.province, qq.city].filter(Boolean).join(" / ") || "—"}</dd><dt>等级</dt><dd>{text(qq.level)}</dd><dt>更新时间</dt><dd>{formatDateTime(core.updated_at as string | number | null)}</dd></dl></Panel><GroupFavorabilityPanel favorability={favorability} eyebrow="RELATION / FAVORABILITY" /></div>
      <Panel eyebrow="PROFILE / SOURCE-AWARE" title="结构化画像（不显示原始 profile_text）"><dl className="compact-kv"><dt>用户明确更正</dt><dd>{Object.keys(record(core.user_corrections)).length ? Object.entries(record(core.user_corrections)).map(([key, item]) => `${key}=${text(item)}`).join("；") : "无"}</dd><dt>结构化字段</dt><dd>{Object.entries(structured).filter(([, item]) => typeof item !== "object").slice(0, 20).map(([key, item]) => `${key}=${text(item)}`).join("；") || "暂无白名单字段"}</dd><dt>头像分析状态</dt><dd>{Object.keys(record(core.avatar_analysis)).length ? "已有受控分析" : "未分析"}</dd><dt>证据范围</dt><dd>{groupId ? `群 ${groupId}` : "全局"}</dd></dl></Panel>
      <Panel eyebrow="ACTIONS / EXPLICIT TARGET" title="画像维护"><div className="form-grid"><label>更正字段<input value={field} onChange={(event) => setField(event.target.value)} placeholder="例如 nickname_preference" /></label><label>更正值<input value={value} onChange={(event) => setValue(event.target.value)} /></label></div><div className="inline-controls"><button className="button" type="button" disabled={!field || !value || run.isPending} onClick={() => run.mutate("correct")}>保存用户明确更正</button><button className="button button-secondary" type="button" disabled={!groupId || !botId || run.isPending} onClick={() => run.mutate("refresh")}>按群实时刷新</button><button className="button button-secondary" type="button" disabled={run.isPending} onClick={() => run.mutate("avatar")}>刷新头像分析</button><button className="button button-danger" type="button" disabled={run.isPending} onClick={() => window.confirm(`确认清除 QQ ${userId} 的头像分析？`) && run.mutate("clear-avatar")}>清除头像分析</button></div><p className="muted-copy">群刷新会核对 Bot、群和成员三元关系；缺少在线 Bot 或 membership 时后端会拒绝。</p></Panel>
    </>}</QueryBoundary>}
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </div>;
}

function GroupTable({ rows, onOpen }: { rows: GroupListItem[]; onOpen: (id: string) => void }) {
  return <Panel eyebrow="CACHE / GROUPS" title="群目录快照"><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>群</th><th>群 ID</th><th>关系</th><th>开关</th><th>关联 Bot</th><th>成员</th><th>操作</th></tr></thead><tbody>{rows.map((item) => <tr key={item.group_id}><td><div className="table-identity"><IdentityAvatar src={item.avatar_url} label={item.group_name || item.group_id} /><strong>{item.group_name || "未缓存群名"}</strong></div></td><td><code>{item.group_id}</code></td><td><StateBadge tone={item.membership_state === "confirmed" ? "ok" : item.membership_state === "configured" ? "info" : "warn"}>{item.membership_state === "confirmed" ? "已确认" : item.membership_state === "configured" ? "仅配置" : "未确认候选"}</StateBadge></td><td><StateBadge tone={item.enabled ? "ok" : "unknown"}>{item.enabled ? "启用" : "停用"}</StateBadge></td><td>{(item.bot_ids || item.bot_self_ids).join("、") || "未确认"}</td><td>{item.member_count ?? "—"}</td><td><button className="button button-secondary" type="button" onClick={() => onOpen(item.group_id)}>打开详情</button></td></tr>)}</tbody></table></div></Panel>;
}

function GroupDetail({ section }: { section: string }) {
  const { params, set } = useUrlState();
  const groupId = params.get("group_id") ?? "";
  const apiSections = section === "knowledge" ? ["knowledge", "style", "memes"] : section === "members" ? ["personas", "aliases"] : section === "peer-bots" ? [] : ["schedule", "agent-state", "memes"];
  const detail = useQuery({ queryKey: ["group-business", groupId, apiSections.join("+")], queryFn: async ({ signal }) => Object.fromEntries(await Promise.all(apiSections.map(async (name) => [name, await resources.groupBusiness(groupId, name as Parameters<typeof resources.groupBusiness>[1], signal)]))), enabled: Boolean(groupId && section !== "peer-bots") });
  const [memberId, setMemberId] = useState("");
  const [aliasText, setAliasText] = useState("");
  const [scheduleText, setScheduleText] = useState("");
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const history = useDiagnosticHistory("group-detail");
  const run = useMutation({ mutationFn: async (action: string) => action === "knowledge" || action === "style" ? resources.rebuildGroup(groupId, action) : action === "alias-save" ? resources.saveGroupAliases(groupId, memberId, aliasText) : action === "alias-delete" ? resources.deleteGroupAliases(groupId, memberId) : action === "schedule-save" ? resources.saveGroupSchedule(groupId, scheduleEnabled, scheduleText) : resources.generateGroupSchedule(groupId), onSuccess: (result) => { history.record(resultDiagnostic(result)); void detail.refetch(); }, onError: (error) => history.record(diagnosticFromError(error)) });
  const byName = record(detail.data);
  const aliases = records(record(byName.aliases).aliases);
  const profiles = records(record(byName.personas).profiles);
  const memes = records(record(byName.memes).items ?? record(byName.memes).memes);
  useEffect(() => { const schedule = record(byName.schedule); if (typeof schedule.schedule_prompt === "string") setScheduleText(schedule.schedule_prompt); if (typeof schedule.enabled === "boolean") setScheduleEnabled(schedule.enabled); }, [byName.schedule]);
  return <div className="page-stack">
    <PageHeader index={`群信息 / ${section === "knowledge" ? "知识与风格" : section === "members" ? "成员与别名" : section === "peer-bots" ? "Peer Bot 协作" : "群详情"}`} title="群详情" description="成员画像、别名、群风格、知识、梗、Agent 状态、Peer Bot 协作和计划均按当前群懒加载。" actions={<input value={groupId} onChange={(event) => set("group_id", event.target.value)} placeholder="群 ID" inputMode="numeric" />} />
    {!groupId ? <EmptyState code="group_id_required">从群列表选择群，或输入群 ID。</EmptyState> : section === "peer-bots" ? <GroupPeerBotsPanel groupId={groupId} /> : <QueryBoundary isPending={detail.isPending} error={detail.error}>
      {section === "members" && <><GroupFavorabilityPanel favorability={record(record(byName.personas).group_favorability)} /><GroupMembersPanel profiles={profiles} /></>}
      {section === "knowledge" ? <><div className="summary-grid"><Panel eyebrow="GROUP / KNOWLEDGE" title="群知识"><p>{text(record(byName.knowledge).summary ?? record(byName.knowledge).knowledge, "暂无知识摘要")}</p></Panel><Panel eyebrow="GROUP / STYLE" title="群风格"><p>{text(record(byName.style).summary ?? record(byName.style).style, "暂无风格摘要")}</p></Panel></div><Panel eyebrow="ACTIONS / REBUILD" title="重建任务"><div className="inline-controls"><button className="button" type="button" disabled={run.isPending} onClick={() => window.confirm(`确认重建群 ${groupId} 的知识？`) && run.mutate("knowledge")}>重建群知识</button><button className="button button-secondary" type="button" disabled={run.isPending} onClick={() => window.confirm(`确认重建群 ${groupId} 的风格？`) && run.mutate("style")}>重建群风格</button></div></Panel><Panel eyebrow="GROUP / MEMES" title={`群梗（${memes.length}）`}>{memes.length ? <ul className="business-list">{memes.map((item, index) => <li key={`${text(item.term)}:${index}`}><strong>{text(item.term, `群梗 ${index + 1}`)}</strong><span>{text(item.meaning ?? item.description)}</span></li>)}</ul> : <EmptyState code="group_memes_empty">暂无群梗记录。</EmptyState>}</Panel></> : section === "members" ? <GroupAliasesPanel aliases={aliases} memberId={memberId} aliasText={aliasText} pending={run.isPending} onMemberIdChange={setMemberId} onAliasTextChange={setAliasText} onSave={() => run.mutate("alias-save")} onDelete={() => window.confirm(`确认删除群 ${groupId} 中成员 ${memberId} 的别名？`) && run.mutate("alias-delete")} /> : <><div className="summary-grid"><Panel eyebrow="AGENT / STATE" title="群内 Agent 状态"><dl className="compact-kv"><dt>心情</dt><dd>{text(record(byName["agent-state"]).mood)}</dd><dt>能量</dt><dd>{text(record(byName["agent-state"]).energy)}</dd><dt>待处理</dt><dd>{text(record(byName["agent-state"]).pending)}</dd></dl></Panel><Panel eyebrow="GROUP / SCHEDULE" title="群作息与计划"><label className="checkbox-label"><input type="checkbox" checked={scheduleEnabled} onChange={(event) => setScheduleEnabled(event.target.checked)} />启用群作息</label><textarea value={scheduleText} onChange={(event) => setScheduleText(event.target.value)} rows={8} placeholder="群作息表" /><div className="inline-controls"><button className="button" type="button" disabled={run.isPending} onClick={() => run.mutate("schedule-save")}>保存计划</button><button className="button button-secondary" type="button" disabled={run.isPending} onClick={() => run.mutate("schedule-generate")}>自动生成草稿</button></div></Panel></div><Panel eyebrow="GROUP / OBSERVATION" title="可观察状态"><dl className="compact-kv"><dt>群 ID</dt><dd><code>{groupId}</code></dd><dt>群梗数量</dt><dd>{memes.length}</dd><dt>调度启用</dt><dd>{scheduleEnabled ? "是" : "否"}</dd><dt>诊断边界</dt><dd>不展示隐藏思维链或原始聊天正文</dd></dl></Panel></>}
    </QueryBoundary>}
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </div>;
}

function StickerCatalog({ rows, onOpen }: { rows: StickerListItem[]; onOpen: (id: string) => void }) {
  return <Panel eyebrow="INDEX / STICKERS" title="持久贴纸索引"><div className="sticker-grid">{rows.map((item) => <article key={item.filename}><img src={item.thumbnail_url} alt={item.description || item.filename} loading="lazy" referrerPolicy="no-referrer" /><div><strong>{item.filename}</strong><p>{item.description || "未标注"}</p><small>{formatInteger(item.size_bytes)} B · {[...item.mood_tags, ...item.scene_tags].join(" / ") || "无标签"}</small><button className="button button-secondary" type="button" onClick={() => onOpen(item.filename)}>编辑</button></div></article>)}</div></Panel>;
}

function StickerOperations({ section }: { section: string }) {
  const { params, set } = useUrlState();
  const selected = params.get("sticker") ?? "";
  const [file, setFile] = useState<File | null>(null);
  const [uploadDescription, setUploadDescription] = useState("");
  const [description, setDescription] = useState("");
  const [moodTags, setMoodTags] = useState("");
  const [sceneTags, setSceneTags] = useState("");
  const history = useDiagnosticHistory("sticker-operations");
  const queryClient = useQueryClient();
  const selectedQuery = useQuery({ queryKey: ["sticker-selected", selected], queryFn: ({ signal }) => resources.stickers(1, 20, selected, signal), enabled: Boolean(selected) });
  const selectedItem = useMemo(() => selectedQuery.data?.items.find((item) => item.filename === selected), [selected, selectedQuery.data]);
  useEffect(() => { if (selectedItem) { setDescription(selectedItem.description || ""); setMoodTags(selectedItem.mood_tags.join(", ")); setSceneTags(selectedItem.scene_tags.join(", ")); } }, [selectedItem]);
  const run = useMutation({ mutationFn: async (action: "upload" | "save" | "delete" | "rescan" | "rebuild") => { if (action === "upload") { if (!file) throw new Error("file_required"); return resources.uploadSticker(file, uploadDescription); } if (action === "save") return resources.updateSticker(selected, { description, mood_tags: moodTags.split(/[,，]/).map((item) => item.trim()).filter(Boolean), scene_tags: sceneTags.split(/[,，]/).map((item) => item.trim()).filter(Boolean) }); if (action === "delete") return resources.deleteSticker(selected); if (action === "rescan") return resources.rescanStickers(); return resources.rebuildStickerIndex(); }, onSuccess: (result) => { history.record(resultDiagnostic(result)); void queryClient.invalidateQueries({ queryKey: ["management-data", "stickers"] }); void selectedQuery.refetch(); }, onError: (error) => history.record(diagnosticFromError(error)) });
  return <div className="page-stack">
    <PageHeader index={`表情包 / ${section === "index" ? "索引任务" : "上传与编辑"}`} title={section === "index" ? "表情包索引" : "上传与编辑"} description="上传限制由后端校验；删除会移动到库内回收目录，结果未知时不会自动重试。" />
    {section === "index" ? <Panel eyebrow="INDEX / TASKS" title="索引维护"><div className="inline-controls"><button className="button" type="button" disabled={run.isPending} onClick={() => run.mutate("rescan")}>增量扫描</button><button className="button button-secondary" type="button" disabled={run.isPending} onClick={() => run.mutate("rebuild")}>后台重建管理投影</button></div></Panel> : <><Panel eyebrow="UPLOAD / NEW" title="上传新表情包"><div className="form-grid"><label>图片文件<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label><label>描述<input value={uploadDescription} onChange={(event) => setUploadDescription(event.target.value)} /></label></div><button className="button" type="button" disabled={!file || run.isPending} onClick={() => run.mutate("upload")}>上传并写入索引</button></Panel><Panel eyebrow="EDIT / EXISTING" title="编辑现有表情包"><div className="form-grid"><label>文件名<input value={selected} onChange={(event) => set("sticker", event.target.value)} placeholder="从目录页选择" /></label><label>描述<input value={description} onChange={(event) => setDescription(event.target.value)} /></label><label>心情标签<input value={moodTags} onChange={(event) => setMoodTags(event.target.value)} /></label><label>场景标签<input value={sceneTags} onChange={(event) => setSceneTags(event.target.value)} /></label></div><div className="inline-controls"><button className="button" type="button" disabled={!selectedItem || run.isPending} onClick={() => run.mutate("save")}>保存 metadata</button><button className="button button-danger" type="button" disabled={!selectedItem || run.isPending} onClick={() => window.confirm(`确认将 ${selected} 移到回收目录？`) && run.mutate("delete")}>移到回收目录</button></div>{selected && !selectedQuery.isPending && !selectedItem && <p className="error-copy">未找到完全匹配的文件名，请返回目录重新选择。</p>}</Panel></>}
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </div>;
}
