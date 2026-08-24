import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useBot } from "../app/BotContext";
import { resources } from "../api/resources";
import type { TokenUsageRow } from "../api/types";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { formatInteger } from "../lib/format";

type WindowKey = "24h" | "7d" | "30d" | "all";
type SortKey = "total_tokens" | "call_count" | "prompt_tokens" | "completion_tokens";
const WINDOWS: Array<{ key: WindowKey; label: string }> = [{ key: "24h", label: "最近 24 小时" }, { key: "7d", label: "最近 7 天" }, { key: "30d", label: "最近 30 天" }, { key: "all", label: "累计" }];

function rowLabel(row: TokenUsageRow) {
  return row.model || row.provider || row.purpose_label || row.purpose || row.group_label || row.group_id || row.label || row.bucket || "未标注";
}

export function TokenStatisticsPage() {
  const { botId } = useBot();
  const [windowKey, setWindowKey] = useState<WindowKey>("24h");
  const [distribution, setDistribution] = useState<"model" | "provider" | "purpose" | "group">("model");
  const [sortKey, setSortKey] = useState<SortKey>("total_tokens");
  const query = useQuery({ queryKey: ["token-metrics", windowKey, botId], queryFn: ({ signal }) => resources.metrics(windowKey, botId, signal) });
  const data = query.data;
  const rows = useMemo(() => {
    if (!data) return [];
    const selected = distribution === "model" ? data.by_model : distribution === "provider" ? data.provider_usage : distribution === "purpose" ? data.by_purpose : data.by_group;
    return [...(selected ?? [])].sort((left, right) => Number(right[sortKey] || 0) - Number(left[sortKey] || 0));
  }, [data, distribution, sortKey]);
  const total = data?.total ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, call_count: 0 };
  const average = total.call_count ? total.total_tokens / total.call_count : 0;
  return (
    <div className="page-stack">
      <PageHeader index="03" title="Token 统计" description="读取同一份本地 Token 账本，展示 Prompt/Completion 分解、调用趋势和模型、供应商、用途、群聊分布。供应商额度与本地消耗分开呈现。" />
      <div className="segmented-control" role="tablist" aria-label="统计时间范围">{WINDOWS.map((item) => <button key={item.key} type="button" role="tab" aria-selected={windowKey === item.key} onClick={() => setWindowKey(item.key)}>{item.label}</button>)}</div>
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {data && <>
          <section className="metric-rack" aria-label="Token 总览">
            <article><span>Prompt Token</span><strong>{formatInteger(total.prompt_tokens)}</strong><small>输入与上下文</small></article>
            <article><span>Completion Token</span><strong>{formatInteger(total.completion_tokens)}</strong><small>模型可见输出</small></article>
            <article><span>Total Token</span><strong>{formatInteger(total.total_tokens)}</strong><small>{formatInteger(total.call_count)} 次模型调用</small></article>
            <article><span>平均每次调用</span><strong>{formatInteger(Math.round(average))}</strong><small>{data.series?.[0]?.label || data.series?.[0]?.bucket || "—"} → {data.series?.at(-1)?.label || data.series?.at(-1)?.bucket || "—"}</small></article>
          </section>
          <Panel eyebrow="TOKEN LEDGER / SERIES" title="Prompt 与 Completion 趋势">
            {data.series?.length ? <TokenSeriesChart rows={data.series} /> : <EmptyState code="token_series_empty">当前范围没有模型调用记录。</EmptyState>}
          </Panel>
          <div className="overview-grid">
            <Panel eyebrow="DISTRIBUTION" title="消耗分布" action={<div className="inline-controls"><select aria-label="分布维度" value={distribution} onChange={(event) => setDistribution(event.target.value as typeof distribution)}><option value="model">按模型</option><option value="provider">按供应商</option><option value="purpose">按用途</option><option value="group">按群聊</option></select><select aria-label="排序字段" value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}><option value="total_tokens">总 Token</option><option value="call_count">调用次数</option><option value="prompt_tokens">Prompt</option><option value="completion_tokens">Completion</option></select></div>}>
              {rows.length ? <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>项目</th><th>调用</th><th>Prompt</th><th>Completion</th><th>总计</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${rowLabel(row)}:${index}`}><td>{rowLabel(row)}</td><td>{formatInteger(row.call_count)}</td><td>{formatInteger(row.prompt_tokens)}</td><td>{formatInteger(row.completion_tokens)}</td><td><strong>{formatInteger(row.total_tokens)}</strong></td></tr>)}</tbody></table></div> : <EmptyState code="token_distribution_empty">当前维度没有可展示记录。</EmptyState>}
            </Panel>
            <Panel eyebrow="BILLING / QUOTA" title="费用与供应商额度">
              <p className="billing-notice">{data.billing?.cost_configured ? `${data.billing.currency} ${data.billing.request_cost.toFixed(4)}` : "未配置价格，无法计算费用"}</p>
              <p className="muted-copy">{data.billing?.note}</p>
              <div className="quota-list">{(data.provider_usage ?? []).map((row) => <div key={row.provider}><span>{row.label}</span><strong>{formatInteger(row.total_tokens)}</strong><small>{row.unlimited ? "未设置月度额度" : `${(row.usage_ratio * 100).toFixed(1)}% / ${formatInteger(row.monthly_limit)}`}</small></div>)}</div>
            </Panel>
          </div>
        </>}
      </QueryBoundary>
    </div>
  );
}

function TokenSeriesChart({ rows }: { rows: TokenUsageRow[] }) {
  const max = Math.max(1, ...rows.map((row) => Number(row.total_tokens || 0)));
  return (
    <div className="token-chart" role="img" aria-label={`Token 趋势，共 ${rows.length} 个时间桶`} tabIndex={0}>
      {rows.map((row, index) => {
        const promptHeight = `${Math.max(0, Number(row.prompt_tokens || 0) / max * 100)}%`;
        const completionHeight = `${Math.max(0, Number(row.completion_tokens || 0) / max * 100)}%`;
        const label = row.label || row.bucket || String(index + 1);
        return <div className="token-bar" key={`${row.bucket || label}:${index}`} tabIndex={0} aria-label={`${label}，Prompt ${row.prompt_tokens || 0}，Completion ${row.completion_tokens || 0}，总计 ${row.total_tokens || 0}`} title={`${label}\nPrompt ${formatInteger(row.prompt_tokens)}\nCompletion ${formatInteger(row.completion_tokens)}\n总计 ${formatInteger(row.total_tokens)}`}><i className="token-bar-prompt" style={{ height: promptHeight }} /><i className="token-bar-completion" style={{ height: completionHeight }} /><span>{rows.length <= 24 || index % Math.ceil(rows.length / 12) === 0 ? label.slice(-5) : ""}</span></div>;
      })}
      <div className="token-chart-legend"><span><i className="legend-prompt" />Prompt</span><span><i className="legend-completion" />Completion</span></div>
    </div>
  );
}
