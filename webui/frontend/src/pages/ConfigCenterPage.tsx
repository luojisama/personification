import { Fragment, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { ConfigListItem } from "../api/types";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";

type FilterKey = "modified" | "restart" | "hot" | "advanced" | "secret";
const FILTERS: Array<{ key: FilterKey; label: string }> = [{ key: "modified", label: "仅已修改" }, { key: "restart", label: "需要重启" }, { key: "hot", label: "支持热加载" }, { key: "advanced", label: "高级配置" }, { key: "secret", label: "秘密配置" }];

function useDebounced(value: string, delay: number) {
  const [result, setResult] = useState(value);
  useEffect(() => { const timer = window.setTimeout(() => setResult(value), delay); return () => window.clearTimeout(timer); }, [delay, value]);
  return result;
}

function displayValue(value: unknown) {
  if (typeof value === "string") return value;
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return JSON.stringify(value ?? "", null, 2);
}

function Highlight({ text, needle }: { text: string; needle: string }) {
  const tokens = needle.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return <>{text}</>;
  const expression = new RegExp(`(${tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return <>{text.split(expression).map((part, index) => tokens.some((token) => token.toLocaleLowerCase("zh-CN") === part.toLocaleLowerCase("zh-CN")) ? <mark key={`${part}:${index}`}>{part}</mark> : <Fragment key={`${part}:${index}`}>{part}</Fragment>)}</>;
}

export function ConfigCenterPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search, 300);
  const [group, setGroup] = useState("");
  const [filters, setFilters] = useState<Record<FilterKey, boolean>>({ modified: false, restart: false, hot: false, advanced: false, secret: false });
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<unknown>(null);
  const [operationResult, setOperationResult] = useState<Record<string, unknown> | null>(null);
  const query = useQuery({ queryKey: ["config-center", debouncedSearch, group], queryFn: ({ signal }) => resources.configAll(debouncedSearch, group, signal) });
  const rows = useMemo(() => (query.data?.items ?? []).filter((item) => (!filters.modified || item.modified || item.field_name in draft) && (!filters.restart || item.restart_required) && (!filters.hot || item.hot_reloadable) && (!filters.advanced || item.advanced) && (!filters.secret || item.secret)), [draft, filters, query.data]);
  const save = useMutation({
    mutationFn: () => resources.patchConfig(query.data?.revision ?? "", draft),
    onSuccess: (result) => { setDraft({}); setError(null); setOperationResult(result as unknown as Record<string, unknown>); void queryClient.invalidateQueries({ queryKey: ["config-center"] }); },
    onError: setError,
  });
  const runLegacyAction = async (path: string, confirmMessage: string) => {
    if (!window.confirm(confirmMessage)) return;
    try { const result = await resources.legacyPost(path, {}); setOperationResult(result as Record<string, unknown>); setError(null); }
    catch (caught) { setError(caught); }
  };
  return (
    <div className="page-stack">
      <PageHeader index="24" title="配置中心" description="按注册表分类浏览、分词搜索和类型化编辑。草稿使用 revision 一次原子保存；秘密原值不会发送到浏览器。" actions={<div className="config-save-actions"><span>{Object.keys(draft).length} 项草稿</span><button className="button" type="button" disabled={!Object.keys(draft).length || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "原子保存"}</button></div>} />
      {error != null && <DiagnosticPanel diagnostic={diagnosticFromError(error)} defaultOpen />}
      {operationResult && <Panel eyebrow="OPERATION RESULT" title="最近操作结果"><pre className="safe-json">{JSON.stringify(operationResult, null, 2)}</pre></Panel>}
      <div className="config-layout">
        <aside className="config-category-rail" aria-label="配置分类">
          <button type="button" className={!group ? "active" : ""} onClick={() => setGroup("")}><span>全部配置</span><b>{query.data?.total ?? 0}</b></button>
          {(query.data?.groups ?? []).map((name) => <button type="button" className={group === name ? "active" : ""} key={name} onClick={() => setGroup(name)}><span>{name}</span><b>{query.data?.group_counts[name] ?? 0}</b><small>{query.data?.modified_counts[name] ? `${query.data.modified_counts[name]} 已修改` : ""}</small></button>)}
        </aside>
        <div className="config-main">
          <Panel eyebrow="FILTER / CONFIG REGISTRY" title="快速筛选">
            <div className="config-search-row"><label><span className="sr-only">搜索配置</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="配置键、中文名称、说明、供应商、模型或别名" /></label><div className="filter-chips">{FILTERS.map((filter) => <button type="button" aria-pressed={filters[filter.key]} key={filter.key} onClick={() => setFilters((current) => ({ ...current, [filter.key]: !current[filter.key] }))}>{filter.label}</button>)}</div></div>
          </Panel>
          <Panel eyebrow="MODEL / MEDIA TOOLS" title="模型与媒体配置工具">
            <div className="dossier-actions"><Link className="button button-secondary" to="/model-tests">模型与视频测试</Link><Link className="button button-secondary" to="/routes">查看路由能力证据</Link><button className="button button-secondary" type="button" onClick={() => void runLegacyAction("/config/search-engines/speed-test", "将对已配置搜索引擎产生真实网络请求，确认继续吗？")}>搜索速度测试</button><button className="button button-secondary" type="button" onClick={() => void runLegacyAction("/config/apply-recommended", "将应用服务端推荐配置并写入配置文件，确认继续吗？")}>应用推荐配置</button></div>
          </Panel>
          <QueryBoundary isPending={query.isPending} error={query.error}>
            {rows.length ? <div className="config-entry-list">{rows.map((item) => <ConfigEditor key={item.field_name} item={item} search={debouncedSearch} draftValue={draft[item.field_name]} changed={item.field_name in draft} onChange={(value) => setDraft((current) => ({ ...current, [item.field_name]: value }))} onReset={() => setDraft((current) => { const next = { ...current }; delete next[item.field_name]; return next; })} />)}</div> : <EmptyState code="config_filter_empty">没有符合当前分类与筛选条件的配置。</EmptyState>}
          </QueryBoundary>
        </div>
      </div>
    </div>
  );
}

function ConfigEditor({ item, search, draftValue, changed, onChange, onReset }: { item: ConfigListItem; search: string; draftValue: unknown; changed: boolean; onChange: (value: unknown) => void; onReset: () => void }) {
  const value = changed ? draftValue : item.value;
  const inputId = `config-${item.field_name}`;
  return <article className={`config-entry${changed ? " is-dirty" : ""}`}>
    <header><div><label htmlFor={inputId}><Highlight text={item.display_name} needle={search} /></label><code>{item.field_name}</code></div><div>{item.secret && <StateBadge tone="warn">秘密</StateBadge>}{item.advanced && <StateBadge tone="info">高级</StateBadge>}<StateBadge tone={item.hot_reloadable ? "ok" : "warn"}>{item.hot_reloadable ? "热加载" : "需重启"}</StateBadge></div></header>
    <p><Highlight text={item.description} needle={search} /></p>
    <div className="config-editor-control"><TypedConfigInput id={inputId} item={item} value={value} onChange={onChange} />{changed && <button type="button" className="button button-quiet" onClick={onReset}>撤销草稿</button>}</div>
    {(item.min_value != null || item.max_value != null || item.aliases.length > 0) && <small>范围 {item.min_value ?? "−∞"} – {item.max_value ?? "+∞"}{item.aliases.length ? ` · 别名 ${item.aliases.join("、")}` : ""}</small>}
  </article>;
}

function TypedConfigInput({ id, item, value, onChange }: { id: string; item: ConfigListItem; value: unknown; onChange: (value: unknown) => void }) {
  if (item.value_type === "bool") return <input id={id} type="checkbox" checked={value === true || value === "true"} onChange={(event) => onChange(event.target.checked)} />;
  if (item.choices.length) return <select id={id} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>{item.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select>;
  if (item.value_type === "int" || item.value_type === "float") return <input id={id} type="number" min={item.min_value ?? undefined} max={item.max_value ?? undefined} step={item.value_type === "int" ? 1 : "any"} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />;
  if (["json", "list", "dict", "textarea"].includes(item.value_type) || typeof value === "object") return <textarea id={id} rows={5} value={displayValue(value)} onChange={(event) => onChange(event.target.value)} spellCheck={false} />;
  return <input id={id} type={item.secret ? "password" : "text"} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} autoComplete={item.secret ? "new-password" : "off"} />;
}
