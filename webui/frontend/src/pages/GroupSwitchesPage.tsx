import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { GroupSwitchItem } from "../api/types";
import { useBot } from "../app/BotContext";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { IdentityAvatar } from "../components/IdentityAvatar";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { StateBadge } from "../components/StateBadge";

function sourceLabel(value: GroupSwitchItem["source"]): string {
  return value === "group_config" ? "群配置" : value === "config_file" ? "静态配置" : value === "dynamic" ? "动态白名单" : "未配置";
}

export function GroupSwitchesPage() {
  const { botId } = useBot();
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const search = params.get("search") ?? "";
  const enabled = params.get("enabled") ?? "";
  const [pendingGroup, setPendingGroup] = useState("");
  const history = useDiagnosticHistory("group-switches");
  const queryClient = useQueryClient();
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "page") next.set("page", "1");
    setParams(next);
  };
  const query = useQuery({ queryKey: ["group-switches", page, search, enabled, botId], queryFn: ({ signal }) => resources.groupSwitches(page, 20, { search, enabled, bot_id: botId }, signal), placeholderData: (previous) => previous });
  const update = useMutation({
    mutationFn: ({ groupId, target }: { groupId: string; target: boolean }) => resources.updateGroupSwitch(groupId, target),
    onMutate: ({ groupId }) => setPendingGroup(groupId),
    onSuccess: (diagnostic) => { history.record(diagnostic); void queryClient.invalidateQueries({ queryKey: ["group-switches"] }); },
    onError: (error) => history.record(diagnosticFromError(error)),
    onSettled: () => setPendingGroup(""),
  });
  const requestChange = (item: GroupSwitchItem) => {
    const target = !item.enabled;
    const action = target ? "启用" : "停用";
    if (!window.confirm(`确认${action}群 ${item.group_name || item.group_id}（${item.group_id}）？\n操作将写入 group_config.enabled，并在保存后重新读取确认。`)) return;
    update.mutate({ groupId: item.group_id, target });
  };

  return <div className="page-stack">
    <PageHeader index="群开关" title="群功能开关" description="按页读取本地群目录，明确显示群头像、群号、群名、配置来源与启用状态。未确认群候选默认不进入列表。" actions={<SearchField value={search} onChange={(value) => setFilter("search", value)} placeholder="搜索群号或群名" />} />
    <Panel eyebrow="FILTER / GROUP SWITCHES" title="开关筛选"><div className="inline-controls filter-control-row"><select value={enabled} onChange={(event) => setFilter("enabled", event.target.value)} aria-label="按启用状态筛选"><option value="">全部状态</option><option value="true">仅已启用</option><option value="false">仅已停用</option></select><StateBadge tone="info">当前 Bot {botId || "全部"}</StateBadge>{query.data && <span>启用 {query.data.enabled_total} / 停用 {query.data.disabled_total}</span>}</div></Panel>
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {query.data?.items.length ? <Panel eyebrow="GROUPS / SWITCHES" title={`群开关（本页 ${query.data.items.length} 项）`}><div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>群</th><th>群号</th><th>状态</th><th>来源</th><th>关系</th><th>操作</th></tr></thead><tbody>{query.data.items.map((item) => <tr key={item.group_id}><td><div className="table-identity"><IdentityAvatar src={item.avatar_url} label={item.group_name || item.group_id} /><strong>{item.group_name || "未缓存群名"}</strong></div></td><td><code>{item.group_id}</code></td><td><StateBadge tone={item.enabled ? "ok" : "error"}>{item.enabled ? "启用" : "停用"}</StateBadge></td><td><StateBadge tone={item.source === "none" ? "unknown" : "info"} raw={item.source}>{sourceLabel(item.source)}</StateBadge>{item.static_config_readonly && <small> 静态项由群配置覆盖</small>}</td><td><StateBadge tone={item.membership_state === "confirmed" ? "ok" : "info"}>{item.membership_state === "confirmed" ? "已确认" : "已配置"}</StateBadge></td><td><button className={item.enabled ? "button button-danger" : "button"} type="button" disabled={pendingGroup === item.group_id} onClick={() => requestChange(item)}>{pendingGroup === item.group_id ? "保存并核对…" : item.enabled ? "停用" : "启用"}</button></td></tr>)}</tbody></table></div></Panel> : <EmptyState code="group_switches_empty">当前筛选条件下没有已确认或已配置的群。</EmptyState>}
    </QueryBoundary>
    {query.data && <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={(value) => setFilter("page", String(value))} />}
    {history.diagnostics.map((diagnostic, index) => <DiagnosticPanel key={`${diagnostic.code}:${index}`} diagnostic={diagnostic} defaultOpen={index === 0} />)}
  </div>;
}
