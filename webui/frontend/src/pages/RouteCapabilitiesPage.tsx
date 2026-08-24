import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { CapabilityName, RouteCapabilityItem } from "../api/types";
import { DiagnosticPanel, useDiagnosticHistory } from "../components/DiagnosticPanel";
import { Icon } from "../components/Icon";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { Pagination } from "../components/Pagination";
import { QueryBoundary } from "../components/QueryBoundary";
import { SearchField } from "../components/SearchField";
import { CapabilityMark, StateBadge } from "../components/StateBadge";
import { capabilitySourceLabel, capabilityStateLabel } from "../lib/labels";
import { formatDateTime, shortId } from "../lib/format";

const CAPABILITY_LABELS: Record<CapabilityName, string> = {
  image_input: "图片",
  audio_input: "音频",
  video_input: "视频",
  reasoning: "推理",
  function_call: "函数",
  native_web_search: "原生搜索",
  external_network_access: "Agent 外网",
};

export function RouteCapabilitiesPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const query = useQuery({
    queryKey: ["route-capabilities", page, search],
    queryFn: ({ signal }) => resources.routes(page, 20, search, signal),
  });

  return (
    <div className="page-stack">
      <PageHeader
        index="02"
        title="路由能力"
        description="能力绑定 Provider、API 类型、URL 指纹、模型和媒体协议。超时与上游故障保持“未知”，不会伪装成“不支持”。"
        actions={<SearchField value={search} onChange={(value) => { setSearch(value); setPage(1); }} placeholder="搜索 Provider、模型或指纹" />}
      />
      <QueryBoundary isPending={query.isPending} error={query.error}>
        {query.data && (
          <>
            {query.data.items.length === 0 ? <EmptyState code="route_capability_list_empty">没有匹配的路由能力记录。</EmptyState> : (
              <div className="route-dossier-list">
                {query.data.items.map((route) => <RouteDossier key={route.route_fingerprint} route={route} />)}
              </div>
            )}
            <Pagination page={query.data.page} totalPages={query.data.total_pages} onChange={setPage} />
          </>
        )}
      </QueryBoundary>
    </div>
  );
}

function RouteDossier({ route }: { route: RouteCapabilityItem }) {
  const queryClient = useQueryClient();
  const history = useDiagnosticHistory(`route:${route.route_fingerprint}`);
  const probe = useMutation({
    mutationFn: () => resources.queueRouteProbe(route.route_fingerprint),
    onSuccess: (result) => {
      history.record(result);
      void queryClient.invalidateQueries({ queryKey: ["route-capabilities"] });
    },
    onError: (error) => history.record(diagnosticFromError(error)),
  });
  const counts = Object.values(route.capabilities).reduce(
    (result, capability) => ({ ...result, [capability.state]: result[capability.state] + 1 }),
    { supported: 0, unsupported: 0, unknown: 0 },
  );

  return (
    <Panel
      as="article"
      className="route-dossier"
      eyebrow={`${route.provider} / ${route.api_type}`}
      title={route.model}
      action={
        <button className="button button-secondary" type="button" disabled={probe.isPending} onClick={() => probe.mutate()}>
          <Icon name="refresh" /> {probe.isPending ? "正在排队" : "异步重测"}
        </button>
      }
    >
      <div className="route-meta-line">
        <code title={route.route_fingerprint}>{shortId(route.route_fingerprint, 10)}</code>
        <span>{route.media_protocol || "未声明媒体协议"}</span>
        <StateBadge tone={route.probe_status === "running" || route.probe_status === "queued" ? "running" : "info"} raw={route.probe_status}>
          {route.probe_status === "running" ? "探针运行中" : route.probe_status === "queued" ? "探针已排队" : "使用缓存证据"}
        </StateBadge>
      </div>
      <div className="capability-grid">
        {(Object.entries(route.capabilities) as Array<[CapabilityName, RouteCapabilityItem["capabilities"][CapabilityName]]>).map(([name, capability]) => (
          <div className="capability-cell" key={name}>
            <CapabilityMark
              state={capability.state}
              label={CAPABILITY_LABELS[name]}
              title={`${capabilityStateLabel(capability.state)} · ${capabilitySourceLabel(capability.source)} · ${capability.detail_code}`}
            />
            <dl>
              <div><dt>证据</dt><dd>{capabilitySourceLabel(capability.source)}</dd></div>
              <div><dt>验证</dt><dd>{formatDateTime(capability.checked_at)}</dd></div>
              <div><dt>诊断</dt><dd><code>{capability.detail_code}</code></dd></div>
            </dl>
          </div>
        ))}
      </div>
      <footer className="route-summary">
        <span>支持 {counts.supported}</span><span>未知 {counts.unknown}</span><span>不支持 {counts.unsupported}</span>
      </footer>
      {history.diagnostics.length > 0 && (
        <div className="diagnostic-stack">
          {history.diagnostics.map((diagnostic, index) => <DiagnosticPanel key={diagnostic.operation_id || diagnostic.trace_id || `${diagnostic.code}:${index}`} diagnostic={diagnostic} defaultOpen={index === 0} />)}
        </div>
      )}
    </Panel>
  );
}
