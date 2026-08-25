import { Fragment, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { ConfigListItem } from "../api/types";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";

type FilterKey = "modified" | "restart_required" | "hot_reloadable" | "advanced" | "secret" | "invalid";
const FILTERS: Array<{ key: FilterKey; label: string }> = [{ key: "modified", label: "仅已修改" }, { key: "restart_required", label: "需要重启" }, { key: "hot_reloadable", label: "支持热加载" }, { key: "advanced", label: "高级配置" }, { key: "secret", label: "秘密配置" }, { key: "invalid", label: "验证错误" }];

function useDebounced(value: string, delay: number) {
  const [result, setResult] = useState(value);
  useEffect(() => { const timer = window.setTimeout(() => setResult(value), delay); return () => window.clearTimeout(timer); }, [delay, value]);
  return result;
}

function Highlight({ text, needle }: { text: string; needle: string }) {
  const tokens = needle.trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return <>{text}</>;
  const expression = new RegExp(`(${tokens.map((token) => token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return <>{text.split(expression).map((part, index) => tokens.some((token) => token.toLocaleLowerCase("zh-CN") === part.toLocaleLowerCase("zh-CN")) ? <mark key={`${part}:${index}`}>{part}</mark> : <Fragment key={`${part}:${index}`}>{part}</Fragment>)}</>;
}

export function ConfigCenterPage() {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const [search, setSearch] = useState(params.get("search") ?? "");
  const debouncedSearch = useDebounced(search, 300);
  const group = params.get("group") ?? "";
  const filters = Object.fromEntries(FILTERS.map((item) => [item.key, params.get(item.key) === "1"])) as Record<FilterKey, boolean>;
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<unknown>(null);
  const history = useDiagnosticHistory("config-center");
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (debouncedSearch) next.set("search", debouncedSearch); else next.delete("search");
    next.set("page", "1");
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  // Only synchronize a settled search value; including params would loop on every keypress.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);
  const setUrlValue = (key: string, value: string) => { const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key); if (key !== "page") next.set("page", "1"); setParams(next); };
  const meta = useQuery({ queryKey: ["config-meta"], queryFn: ({ signal }) => resources.configMetadata(signal), staleTime: 60_000 });
  const query = useQuery({ queryKey: ["config-center", page, debouncedSearch, group, filters], queryFn: ({ signal }) => resources.config(page, 20, { search: debouncedSearch, group, ...filters }, signal), placeholderData: (previous) => previous });
  const save = useMutation({
    mutationFn: () => resources.patchConfig(query.data?.revision ?? meta.data?.revision ?? "", draft),
    onSuccess: () => { setDraft({}); setError(null); void queryClient.invalidateQueries({ queryKey: ["config-center"] }); void queryClient.invalidateQueries({ queryKey: ["config-meta"] }); },
    onError: setError,
  });
  const runTool = async (kind: "speed" | "recommended", confirmMessage: string) => {
    if (!window.confirm(confirmMessage)) return;
    try { const result = kind === "speed" ? await resources.searchEngineSpeedTest() : await resources.applyRecommendedConfig(); history.record(result); setError(null); }
    catch (caught) { setError(caught); }
  };
  return (
    <div className="page-stack">
      <PageHeader index="24" title="配置中心" description="按注册表分类浏览、分词搜索和类型化编辑。草稿使用 revision 一次原子保存；秘密原值不会发送到浏览器。" actions={<div className="config-save-actions"><span>{Object.keys(draft).length} 项草稿</span><button className="button" type="button" disabled={!Object.keys(draft).length || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "保存中…" : "原子保存"}</button></div>} />
      {error != null && <DiagnosticPanel diagnostic={diagnosticFromError(error)} defaultOpen />}
      {history.diagnostics.map((diagnostic, index) => <DiagnosticPanel key={`${diagnostic.code}:${index}`} diagnostic={diagnostic} defaultOpen={index === 0} />)}
      <div className="config-layout">
        <aside className="config-category-rail" aria-label="配置分类">
          <button type="button" className={!group ? "active" : ""} onClick={() => setUrlValue("group", "")}><span>全部配置</span><b>{meta.data?.total ?? 0}</b></button>
          {(meta.data?.groups ?? []).map((name) => <button type="button" className={group === name ? "active" : ""} key={name} onClick={() => setUrlValue("group", name)}><span>{name}</span><b>{meta.data?.group_counts[name] ?? 0}</b><small>{meta.data?.modified_counts[name] ? `${meta.data.modified_counts[name]} 已修改` : ""}</small></button>)}
        </aside>
        <div className="config-main">
          <Panel eyebrow="FILTER / CONFIG REGISTRY" title="快速筛选">
            <div className="config-search-row"><label><span className="sr-only">搜索配置</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="配置键、中文名称、说明、供应商、模型或别名" /></label><div className="filter-chips">{FILTERS.map((filter) => <button type="button" aria-pressed={filters[filter.key]} key={filter.key} onClick={() => setUrlValue(filter.key, filters[filter.key] ? "" : "1")}>{filter.label}</button>)}</div></div>
          </Panel>
          <Panel eyebrow="MODEL / MEDIA TOOLS" title="模型与媒体配置工具">
            <div className="dossier-actions"><Link className="button button-secondary" to="/runtime/model-tests/video-turn">模型与视频测试</Link><Link className="button button-secondary" to="/runtime/routes/capabilities">查看路由能力证据</Link><button className="button button-secondary" type="button" onClick={() => void runTool("speed", "将对已配置搜索引擎产生真实网络请求，确认继续吗？")}>搜索速度测试</button><button className="button button-secondary" type="button" onClick={() => void runTool("recommended", "将应用服务端推荐配置并写入配置文件，确认继续吗？")}>应用推荐配置</button></div>
          </Panel>
          <QueryBoundary isPending={query.isPending} error={query.error}>
            {query.data?.items.length ? <div className="config-entry-list">{query.data.items.map((item) => <ConfigEditor key={item.field_name} item={item} search={debouncedSearch} draftValue={draft[item.field_name]} changed={item.field_name in draft} onChange={(value) => setDraft((current) => ({ ...current, [item.field_name]: value }))} onReset={() => setDraft((current) => { const next = { ...current }; delete next[item.field_name]; return next; })} />)}</div> : <EmptyState code="config_filter_empty">没有符合当前分类与筛选条件的配置。</EmptyState>}
          </QueryBoundary>
          {query.data && <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={(value) => setUrlValue("page", String(value))} />}
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
  if (item.value_type === "list") {
    const items = Array.isArray(value) ? value : [];
    return <StructuredListInput id={id} items={items} modelRoutes={item.field_name === "personification_api_pools"} onChange={onChange} />;
  }
  if (item.value_type === "dict" || (value != null && typeof value === "object" && !Array.isArray(value))) {
    return <StructuredObjectInput id={id} value={asRecord(value)} onChange={onChange} />;
  }
  if (item.value_type === "textarea") return <textarea id={id} rows={5} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} />;
  return <input id={id} type={item.secret ? "password" : "text"} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} autoComplete={item.secret ? "new-password" : "off"} />;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function StructuredListInput({ id, items, modelRoutes, onChange }: { id: string; items: unknown[]; modelRoutes: boolean; onChange: (value: unknown[]) => void }) {
  const objectMode = modelRoutes || items.some((item) => item != null && typeof item === "object" && !Array.isArray(item));
  const replace = (index: number, value: unknown) => onChange(items.map((item, current) => current === index ? value : item));
  const move = (index: number, direction: -1 | 1) => {
    const destination = index + direction;
    if (destination < 0 || destination >= items.length) return;
    const next = [...items];
    [next[index], next[destination]] = [next[destination], next[index]];
    onChange(next);
  };
  return <fieldset id={id} className="structured-editor">
    <legend>{modelRoutes ? "模型路由列表" : "列表项"}</legend>
    {items.length ? items.map((item, index) => <article className="structured-item" key={`${id}:${index}`}>
      <header><strong>{modelRoutes ? `路由 ${index + 1}` : `第 ${index + 1} 项`}</strong><div className="structured-actions"><button type="button" className="button button-quiet" aria-label="上移" disabled={index === 0} onClick={() => move(index, -1)}>↑</button><button type="button" className="button button-quiet" aria-label="下移" disabled={index === items.length - 1} onClick={() => move(index, 1)}>↓</button><button type="button" className="button button-danger" onClick={() => onChange(items.filter((_, current) => current !== index))}>删除</button></div></header>
      {objectMode ? <StructuredObjectInput id={`${id}-${index}`} value={asRecord(item)} routeFields={modelRoutes} onChange={(next) => replace(index, next)} /> : <input aria-label={`第 ${index + 1} 项`} value={String(item ?? "")} onChange={(event) => replace(index, event.target.value)} />}
    </article>) : <p className="muted-copy">当前列表为空。新增后才会写入草稿。</p>}
    <button type="button" className="button button-secondary" onClick={() => onChange([...items, objectMode ? {} : ""])}>{modelRoutes ? "新增模型路由" : "新增列表项"}</button>
  </fieldset>;
}

const ROUTE_FIELD_ORDER = ["name", "provider", "api_type", "api_url", "api_key", "model", "media_protocol", "timeout", "max_retries", "priority", "enabled"];

function StructuredObjectInput({ id, value, routeFields = false, onChange }: { id: string; value: Record<string, unknown>; routeFields?: boolean; onChange: (value: Record<string, unknown>) => void }) {
  const keys = Object.keys(value).filter((key) => key !== "_secret_ref").sort((left, right) => {
    if (!routeFields) return left.localeCompare(right, "zh-CN");
    const leftRank = ROUTE_FIELD_ORDER.indexOf(left);
    const rightRank = ROUTE_FIELD_ORDER.indexOf(right);
    return (leftRank < 0 ? ROUTE_FIELD_ORDER.length : leftRank) - (rightRank < 0 ? ROUTE_FIELD_ORDER.length : rightRank) || left.localeCompare(right, "zh-CN");
  });
  const updateKey = (oldKey: string, nextKey: string) => {
    const normalized = nextKey.trim();
    if (!normalized || (normalized !== oldKey && normalized in value)) return;
    const next: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) next[key === oldKey ? normalized : key] = item;
    onChange(next);
  };
  const remove = (key: string) => onChange(Object.fromEntries(Object.entries(value).filter(([current]) => current !== key)));
  const add = () => {
    const preferred = routeFields ? ROUTE_FIELD_ORDER.find((key) => !(key in value)) : undefined;
    let key = preferred ?? "新字段";
    let suffix = 2;
    while (key in value) key = `新字段_${suffix++}`;
    onChange({ ...value, [key]: "" });
  };
  return <div className="structured-object" data-editor="object">
    {keys.length ? keys.map((key) => <div className="structured-field" key={`${id}:${key}`}>
      <input className="structured-key" aria-label="字段名" value={key} onChange={(event) => updateKey(key, event.target.value)} />
      <StructuredScalarInput id={`${id}-${key}`} fieldName={key} value={value[key]} onChange={(next) => onChange({ ...value, [key]: next })} />
      <button type="button" className="button button-quiet" aria-label={`删除字段 ${key}`} onClick={() => remove(key)}>删除字段</button>
    </div>) : <p className="muted-copy">当前对象为空，可按字段逐项编辑。</p>}
    <button type="button" className="button button-quiet" onClick={add}>新增字段</button>
  </div>;
}

function StructuredScalarInput({ id, fieldName, value, onChange }: { id: string; fieldName: string; value: unknown; onChange: (value: unknown) => void }) {
  if (Array.isArray(value)) return <StructuredListInput id={id} items={value} modelRoutes={false} onChange={onChange} />;
  if (value != null && typeof value === "object") return <StructuredObjectInput id={id} value={asRecord(value)} onChange={onChange} />;
  if (typeof value === "boolean") return <label className="checkbox-label"><input id={id} type="checkbox" checked={value} onChange={(event) => onChange(event.target.checked)} />启用</label>;
  if (typeof value === "number") return <input id={id} type="number" value={String(value)} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} />;
  const secret = /(?:api[_-]?key|token|secret|password|cookie)/i.test(fieldName);
  return <input id={id} type={secret ? "password" : "text"} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} autoComplete={secret ? "new-password" : "off"} />;
}
