import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { diagnosticFromError, safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { OperationDiagnostic } from "../api/types";
import { asRecord, BusinessTable, recordsAt, SafeStatus, textAt, type BusinessRecord } from "../components/BusinessTable";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";

export function ModelTestsPage() {
  const [prompt, setPrompt] = useState("用一句话说明当前模型已连通。");
  const [video, setVideo] = useState<File | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<unknown>(null);
  const persona = useQuery({ queryKey: ["persona-prompt-preview"], queryFn: ({ signal }) => resources.personaPromptPreview(signal), enabled: false });
  const run = useMutation({ mutationFn: async (mode: "single" | "all") => {
    if (!window.confirm(`将向${mode === "single" ? "当前模型" : "全部已配置模型"}发起真实外部调用并产生额度消耗，确认继续吗？`)) return null;
    return resources.modelChat(mode, prompt);
  }, onSuccess: (value) => { if (value != null) setResult(value); setError(null); }, onError: setError });
  const probeVideo = useMutation({ mutationFn: async () => {
    if (!video) throw new Error("请先选择视频文件");
    if (!window.confirm(`将上传 ${video.name}（${(video.size / 1024 / 1024).toFixed(1)} MB）并调用视频路由，可能产生供应商额度消耗。确认继续吗？`)) return null;
    return resources.videoRouteProbe(video);
  }, onSuccess: (value) => { if (value != null) setResult(value); setError(null); }, onError: setError });
  const videoTurn = useMutation({ mutationFn: async () => {
    if (!video) throw new Error("请先选择视频文件");
    if (!window.confirm(`将上传 ${video.name} 并进入与真实聊天一致的完整 Agent 链路。最终回复只在 WebUI 捕获，绝不发送 QQ。确认继续吗？`)) return null;
    return resources.videoTurnTest(video, prompt);
  }, onSuccess: (value) => { if (value != null) setResult(value); setError(null); }, onError: setError });
  return <div className="page-stack">
    <PageHeader index="05" title="模型测试" description="单路由、全路由、人设 Prompt 与媒体测试入口。视频路由探针只证明路由接受视频；完整视频回合必须经过语义帧、Agent、工具、证据门与输出检查。" />
    {error != null && <DiagnosticPanel diagnostic={diagnosticFromError(error)} defaultOpen />}
    <div className="overview-grid">
      <Panel eyebrow="PROVIDER / CHAT" title="单路由与全路由对照"><label className="stacked-field">测试消息<textarea rows={4} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label><div className="dossier-actions"><button className="button button-secondary" type="button" disabled={run.isPending} onClick={() => run.mutate("single")}>测试当前路由</button><button className="button button-secondary" type="button" disabled={run.isPending} onClick={() => run.mutate("all")}>测试全部路由</button></div></Panel>
      <Panel eyebrow="PERSONA / PROMPT" title="人设 Prompt 预览"><p>读取当前服务端实际生效的人设来源和质量信息，不在浏览器重新拼接 Prompt。</p><button className="button button-secondary" type="button" onClick={() => void persona.refetch()}>加载当前 Prompt</button><QueryBoundary isPending={persona.isFetching} error={persona.error}>{persona.data && <PersonaPromptResult value={persona.data} />}</QueryBoundary></Panel>
      <Panel eyebrow="VIDEO / ROUTE PROBE" title="视频路由探针"><p>只验证配置的视频理解路线，不进入聊天 Agent，也不发送 QQ。</p><input type="file" accept="video/*,.mkv,.avi" onChange={(event) => setVideo(event.target.files?.[0] ?? null)} /><button className="button button-secondary" type="button" disabled={!video || probeVideo.isPending} onClick={() => probeVideo.mutate()}>{probeVideo.isPending ? "探针运行中…" : "确认并运行路由探针"}</button></Panel>
      <Panel eyebrow="VIDEO / FULL TURN" title="完整视频回合"><p>使用与真实 QQ 回合一致的语义帧、规划、Agent、媒体工具、证据门和可见输出门。发送接口被替换为只捕获代理，不会触达 QQ。</p><button className="button" type="button" disabled={!video || videoTurn.isPending} onClick={() => videoTurn.mutate()}>{videoTurn.isPending ? "完整回合运行中…" : "确认并运行完整无发送回合"}</button><div className="security-manifest">成功条件同时要求捕获可见回复和关联成功的 <code>vision_analyze</code> 视频证据；仅有路由探针结果不会通过。</div></Panel>
    </div>
    {result != null && <Panel eyebrow="TEST RESULT" title="最近测试结果"><ModelTestResult value={result} /></Panel>}
  </div>;
}

function resultDiagnostic(row: BusinessRecord): OperationDiagnostic | null {
  const nested = asRecord(row.diagnostic);
  const source = Object.keys(nested).length ? nested : row;
  if (textAt(source, "code", "diagnostic_code") === "—") return null;
  return safeDiagnostic(source as Partial<OperationDiagnostic>);
}

function PersonaPromptResult({ value }: { value: unknown }) {
  const row = asRecord(value);
  const diagnostic = resultDiagnostic(row);
  const content = textAt(row, "content");
  return <div className="page-stack">
    <dl className="compact-kv">
      <dt>来源</dt><dd>{textAt(row, "source")}</dd>
      <dt>状态</dt><dd><SafeStatus row={{ state: row.exists === true ? "available" : "unavailable" }} /></dd>
      <dt>类型</dt><dd>{row.is_file === true ? "文件" : "运行时内联配置"}</dd>
      <dt>大小</dt><dd>{typeof row.size === "number" ? `${row.size} bytes` : "—"}</dd>
    </dl>
    {content !== "—" ? <pre className="prompt-preview" aria-label="当前人设 Prompt">{content}</pre> : <EmptyState code="persona_prompt_content_empty">当前来源没有可展示的 Prompt 内容。</EmptyState>}
    {diagnostic && <DiagnosticPanel diagnostic={diagnostic} />}
  </div>;
}

function ModelTestResult({ value }: { value: unknown }) {
  const row = asRecord(value);
  const diagnostic = resultDiagnostic(row);
  const summary = asRecord(row.summary);
  const providerRows = recordsAt(row, "results");
  const evidenceRows = recordsAt(row, "media_evidence");
  const checkRows = recordsAt(row, "categories").flatMap((category) => {
    const categoryName = textAt(category, "name");
    return recordsAt(category, "checks").map((check) => ({ ...check, category: categoryName }));
  });
  const visibleReply = textAt(row, "reply", "content");
  const traceId = textAt(row, "trace_id");
  return <div className="page-stack">
    <dl className="compact-kv">
      <dt>结果</dt><dd><SafeStatus row={{ state: row.ok === true ? "succeeded" : textAt(row, "overall", "state") }} /></dd>
      <dt>诊断码</dt><dd><code>{textAt(row, "code", "diagnosis_code", "diagnostic_code")}</code></dd>
      <dt>耗时</dt><dd>{typeof row.duration_ms === "number" ? `${row.duration_ms} ms` : "—"}</dd>
      <dt>出站</dt><dd>{textAt(row, "outbound")}</dd>
      {traceId !== "—" && <><dt>Trace</dt><dd><Link to={`/runtime/traces/timeline/${encodeURIComponent(traceId)}`}><code>{traceId}</code></Link></dd></>}
    </dl>
    {Object.keys(summary).length > 0 && <div className="metric-ribbon" aria-label="测试统计">
      {Object.entries(summary).filter(([, count]) => typeof count === "number").map(([key, count]) => <article key={key}><span>{key}</span><strong>{String(count)}</strong></article>)}
    </div>}
    {visibleReply !== "—" && <section className="captured-reply"><h3>捕获的可见回复</h3><p>{visibleReply}</p></section>}
    {providerRows.length > 0 && <BusinessTable rows={providerRows} rowKey={(item, index) => `${textAt(item, "name", "model_used")}:${index}`} emptyCode="model_test_provider_empty" emptyText="没有 Provider 对照结果。" columns={[
      { key: "name", label: "Provider", render: (item) => <><strong>{textAt(item, "name")}</strong><br /><code>{textAt(item, "model", "model_used")}</code></> },
      { key: "state", label: "结果", render: (item) => <SafeStatus row={{ state: item.ok === true ? "succeeded" : "failed" }} /> },
      { key: "duration", label: "耗时", render: (item) => typeof item.duration_ms === "number" ? `${item.duration_ms} ms` : "—" },
      { key: "content", label: "安全回复摘要", render: (item) => textAt(item, "content", "error") },
    ]} />}
    {checkRows.length > 0 && <BusinessTable rows={checkRows} rowKey={(item, index) => `${textAt(item, "key")}:${index}`} emptyCode="video_probe_checks_empty" emptyText="没有视频探针步骤。" columns={[
      { key: "category", label: "分类", render: (item) => textAt(item, "category") },
      { key: "label", label: "检查项", render: (item) => <><strong>{textAt(item, "label")}</strong><br /><code>{textAt(item, "key")}</code></> },
      { key: "status", label: "状态", render: (item) => <SafeStatus row={item} /> },
      { key: "detail", label: "证据与建议", render: (item) => <>{textAt(item, "detail")} {textAt(item, "hint") !== "—" && <span className="muted">· {textAt(item, "hint")}</span>}</> },
    ]} />}
    {evidenceRows.length > 0 ? <BusinessTable rows={evidenceRows} rowKey={(item, index) => `${textAt(item, "tool")}:${index}`} emptyCode="video_turn_evidence_empty" emptyText="完整回合没有关联媒体证据。" columns={[
      { key: "tool", label: "媒体工具", render: (item) => <code>{textAt(item, "tool")}</code> },
      { key: "status", label: "采用状态", render: (item) => <SafeStatus row={item} /> },
      { key: "detail", label: "脱敏证据摘要", render: (item) => textAt(item, "detail") },
    ]} /> : row.outbound === "captured_not_sent" && <EmptyState code="video_turn_evidence_empty">完整回合没有关联成功的视频证据，因此不能视为通过。</EmptyState>}
    {diagnostic && <DiagnosticPanel diagnostic={diagnostic} defaultOpen={!diagnostic.ok} />}
  </div>;
}
