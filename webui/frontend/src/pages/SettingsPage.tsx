import { useQuery } from "@tanstack/react-query";

import { resources } from "../api/resources";
import { PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { THEME_META, THEMES, useTheme } from "../theme/theme";

export function SettingsPage() {
  const [theme, setTheme] = useTheme();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => resources.runtimeSettings(signal),
  });

  return (
    <div className="page-stack">
      <PageHeader index="33" title="设置" description="控制浏览器侧主题与动效偏好；服务端配置只展示安全状态，不在本页回显 Secret 或原始配置包。" />
      <div className="settings-grid">
        <Panel className="wide-panel" eyebrow="APPEARANCE / LOCAL" title="取证台主题">
          <div className="theme-grid">
            {THEMES.map((name) => (
              <button className={`theme-specimen theme-${name}${theme === name ? " active" : ""}`} type="button" key={name} onClick={() => setTheme(name)} aria-pressed={theme === name}>
                <span className="specimen-grid" aria-hidden="true"><i /><i /><i /><i /></span>
                <strong>{THEME_META[name].name}</strong>
                <p>{THEME_META[name].description}</p>
                <small>{THEME_META[name].signal}</small>
              </button>
            ))}
          </div>
        </Panel>
        <Panel eyebrow="ACCESSIBILITY / MOTION" title="动效与键盘">
          <ul className="settings-notes">
            <li><StateBadge tone="ok">120–220 ms</StateBadge><span>常规状态切换保持短促，不制造等待假象。</span></li>
            <li><StateBadge tone="ok">系统联动</StateBadge><span>启用“减少动态效果”后，过渡与动画自动缩短到 1 ms。</span></li>
            <li><StateBadge tone="ok">焦点可见</StateBadge><span>导航、按钮、筛选和诊断折叠均保留键盘焦点轮廓。</span></li>
          </ul>
        </Panel>
        <Panel eyebrow="RUNTIME / SAFE VIEW" title="服务端配置状态">
          <QueryBoundary isPending={settings.isPending} error={settings.error}>
            <dl className="safe-settings-view">
              <div><dt>API 前缀</dt><dd><code>/personification/api/v2</code></dd></div>
              <div><dt>实时协议</dt><dd>SSE + Last-Event-ID</dd></div>
              <div><dt>配置版本</dt><dd><code>{typeof settings.data?.revision === "string" || typeof settings.data?.revision === "number" ? String(settings.data.revision) : "未提供"}</code></dd></div>
              <div><dt>参与策略</dt><dd>{typeof settings.data?.participation_v2_mode === "string" ? `影子开关（${settings.data.participation_v2_mode}）` : "未提供"}</dd></div>
            </dl>
          </QueryBoundary>
        </Panel>
        <Panel className="wide-panel" eyebrow="SECURITY / DISPLAY" title="可见数据边界">
          <div className="security-manifest">
            <p>此管理台只消费服务端白名单 DTO。Trace 详情不会读取隐藏推理、完整 Tool 参数、原始 Tool 结果、Provider 请求/响应、Cookie、API Key 或媒体 Token。</p>
            <code>frontend_trace_allowlist_v1</code>
          </div>
        </Panel>
      </div>
    </div>
  );
}
