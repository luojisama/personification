import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { diagnosticFromError } from "../api/diagnostics";
import { resources } from "../api/resources";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { PageHeader, Panel } from "../components/Panel";
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
      <Panel eyebrow="PERSONA / PROMPT" title="人设 Prompt 预览"><p>读取当前服务端实际生效的人设来源和质量信息，不在浏览器重新拼接 Prompt。</p><button className="button button-secondary" type="button" onClick={() => void persona.refetch()}>加载当前 Prompt</button><QueryBoundary isPending={persona.isFetching} error={persona.error}>{persona.data && <pre className="safe-json">{JSON.stringify(persona.data, null, 2)}</pre>}</QueryBoundary></Panel>
      <Panel eyebrow="VIDEO / ROUTE PROBE" title="视频路由探针"><p>只验证配置的视频理解路线，不进入聊天 Agent，也不发送 QQ。</p><input type="file" accept="video/*,.mkv,.avi" onChange={(event) => setVideo(event.target.files?.[0] ?? null)} /><button className="button button-secondary" type="button" disabled={!video || probeVideo.isPending} onClick={() => probeVideo.mutate()}>{probeVideo.isPending ? "探针运行中…" : "确认并运行路由探针"}</button></Panel>
      <Panel eyebrow="VIDEO / FULL TURN" title="完整视频回合"><p>使用与真实 QQ 回合一致的语义帧、规划、Agent、媒体工具、证据门和可见输出门。发送接口被替换为只捕获代理，不会触达 QQ。</p><button className="button" type="button" disabled={!video || videoTurn.isPending} onClick={() => videoTurn.mutate()}>{videoTurn.isPending ? "完整回合运行中…" : "确认并运行完整无发送回合"}</button><div className="security-manifest">成功条件同时要求捕获可见回复和关联成功的 <code>vision_analyze</code> 视频证据；仅有路由探针结果不会通过。</div></Panel>
    </div>
    {result != null && <Panel eyebrow="TEST RESULT" title="最近测试结果"><pre className="safe-json">{JSON.stringify(result, null, 2)}</pre></Panel>}
  </div>;
}
