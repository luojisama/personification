import { useQuery } from "@tanstack/react-query";

import { resources } from "../api/resources";
import { PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";

function booleanBadge(value: boolean, trueLabel: string, falseLabel: string) {
  return <StateBadge tone={value ? "ok" : "unknown"}>{value ? trueLabel : falseLabel}</StateBadge>;
}

function safeRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function SystemDiagnosticsPage() {
  const multimodal = useQuery({ queryKey: ["multimodal-routes"], queryFn: ({ signal }) => resources.multimodalRoutes(signal) });
  const qzone = useQuery({ queryKey: ["qzone-capabilities"], queryFn: ({ signal }) => resources.qzoneCapabilities(signal) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: ({ signal }) => resources.runtimeSettings(signal) });
  return (
    <div className="page-stack">
      <PageHeader index="06" title="系统诊断" description="只读展示多模态路由、QZone 分项能力、渐进工具、参与策略、情绪 v2 与完整备份边界。存在本地代码不等于生产可用。" />
      <div className="systems-grid">
        <Panel eyebrow="MEDIA / ROUTES" title="音频与视频路由">
          <QueryBoundary isPending={multimodal.isPending} error={multimodal.error}>
            {multimodal.data && <div className="systems-ledger">
              <section><h3>音频</h3><p>{booleanBadge(multimodal.data.audio.route_available, "存在可用路线", "未确认可用路线")}</p><dl><div><dt>主模型原生</dt><dd>{String(multimodal.data.audio.primary_native)}</dd></div><div><dt>ASR Provider</dt><dd><code>{multimodal.data.audio.asr_provider}</code></dd></div><div><dt>回退顺序</dt><dd>{multimodal.data.audio.fallback_order.join(" → ")}</dd></div></dl></section>
              <section><h3>视频</h3><p>{booleanBadge(multimodal.data.video.enabled, "视频理解已启用", "视频理解配置关闭")}</p><dl><div><dt>路线模式</dt><dd><code>{multimodal.data.video.route_mode}</code></dd></div><div><dt>主模型原生</dt><dd>{String(multimodal.data.video.primary_native)}</dd></div><div><dt>回退顺序</dt><dd>{multimodal.data.video.fallback_order.join(" → ")}</dd></div></dl></section>
              <div className="unknown-warning">{multimodal.data.production_verified ? "已完成生产验证" : "仅本地路线快照；真实 QQ、Gemini 与生产部署尚需管理员联调。"} <code>{multimodal.data.diagnostic_code}</code></div>
            </div>}
          </QueryBoundary>
        </Panel>
        <Panel eyebrow="QZONE / MATRIX" title="QZone 分项能力">
          <QueryBoundary isPending={qzone.isPending} error={qzone.error}>
            {qzone.data && <CapabilityObject value={qzone.data} />}
          </QueryBoundary>
        </Panel>
        <Panel className="wide-panel" eyebrow="ROLL-OUT / SAFETY" title="功能开关与迁移边界">
          <QueryBoundary isPending={settings.isPending} error={settings.error}>
            {settings.data && <div className="rollout-ledger">
              <div><span>参与概率 v2</span><StateBadge tone={settings.data.participation_v2_mode === "on" ? "ok" : "running"}>{String(settings.data.participation_v2_mode ?? "unknown")}</StateBadge><small>shadow 只记录新旧差异，不改变实际行为。</small></div>
              <div><span>渐进式工具</span><StateBadge tone={settings.data.tool_disclosure_mode === "off" ? "unknown" : "ok"}>{String(settings.data.tool_disclosure_mode ?? "unknown")}</StateBadge><small>副作用仍绑定原工具；发现过程不执行工具。</small></div>
              <div><span>情绪状态 v2</span><StateBadge tone={settings.data.emotion_v2_mode === "on" ? "ok" : "running"}>{String(settings.data.emotion_v2_mode ?? "unknown")}</StateBadge><small>shadow 写入兼容状态但不影响提示。</small></div>
              <div><span>完整备份</span><StateBadge tone="warn">step-up</StateBadge><small>状态包与 AES-256-GCM 秘密包分离；恢复后端不可用时拒绝执行。</small></div>
            </div>}
          </QueryBoundary>
        </Panel>
      </div>
    </div>
  );
}

function CapabilityObject({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value).filter(([, item]) => typeof item !== "object" || item === null).slice(0, 24);
  const nested = Object.entries(value).filter(([, item]) => typeof item === "object" && item !== null && !Array.isArray(item)).slice(0, 16);
  return <div className="capability-object">
    {entries.map(([key, item]) => <div key={key}><dt>{key}</dt><dd><code>{String(item ?? "")}</code></dd></div>)}
    {nested.map(([key, item]) => {
      const row = safeRecord(item);
      const state = String(row.state ?? row.status ?? "unknown");
      return <div key={key}><dt>{key}</dt><dd><StateBadge tone={state === "supported" || state === "ok" ? "ok" : state === "unsupported" || state === "failed" ? "error" : "unknown"}>{state}</StateBadge> <code>{String(row.detail_code ?? row.diagnostic_code ?? "")}</code></dd></div>;
    })}
  </div>;
}
