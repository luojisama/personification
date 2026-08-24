import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useRuntimeEvents } from "../realtime/RuntimeEventsProvider";
import { THEME_META, THEMES, useTheme } from "../theme/theme";
import { Icon, type IconName } from "./Icon";
import { StateBadge } from "./StateBadge";

const NAV_ITEMS: Array<{ to: string; label: string; short: string; icon: IconName; index: string }> = [
  { to: "/", label: "总览", short: "总览", icon: "overview", index: "01" },
  { to: "/routes", label: "路由能力", short: "路由", icon: "route", index: "02" },
  { to: "/traces", label: "Trace 取证", short: "Trace", icon: "trace", index: "03" },
  { to: "/recovery", label: "恢复队列", short: "恢复", icon: "recovery", index: "04" },
  { to: "/settings", label: "设置", short: "设置", icon: "settings", index: "05" },
];

function nextTheme(current: (typeof THEMES)[number]) {
  return THEMES[(THEMES.indexOf(current) + 1) % THEMES.length] ?? "minimal";
}

export function AppShell() {
  const [theme, setTheme] = useTheme();
  const realtime = useRuntimeEvents();
  const location = useLocation();
  const targetTheme = nextTheme(theme);

  return (
    <>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <div className="app-frame">
        <aside className="evidence-rail" aria-label="管理台主导航">
          <div className="brand-plate">
            <span className="brand-sigil" aria-hidden="true">P/F</span>
            <div>
              <strong>拟人插件</strong>
              <small>事件取证台</small>
            </div>
          </div>
          <nav className="primary-nav">
            {NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.to === "/"}>
                <span className="nav-index">{item.index}</span>
                <Icon name={item.icon} />
                <span className="nav-label">{item.label}</span>
                <span className="nav-short">{item.short}</span>
              </NavLink>
            ))}
          </nav>
          <div className="rail-foot">
            <button
              className="theme-cycle"
              type="button"
              onClick={() => setTheme(targetTheme)}
              aria-label={`切换到 ${THEME_META[targetTheme].name} 主题`}
              title={`切换到 ${THEME_META[targetTheme].name} 主题`}
            >
              <span className="theme-swatch" aria-hidden="true" />
              <span>{THEME_META[theme].name}</span>
            </button>
            <StateBadge
              tone={realtime.state === "open" ? "ok" : realtime.state === "closed" ? "error" : "running"}
              raw={realtime.state}
            >
              {realtime.state === "open" ? "实时事件已连接" : realtime.state === "closed" ? "实时事件已关闭" : "正在连接事件流"}
            </StateBadge>
          </div>
        </aside>
        <div className="workbench">
          <header className="top-status-line">
            <span><Icon name="signal" /> 实时事件 {realtime.events.length}/500</span>
            {realtime.resyncCount > 0 && <span>已执行 REST 重同步 {realtime.resyncCount} 次</span>}
            <code>{location.pathname}</code>
          </header>
          <main id="main-content" className="main-workspace" tabIndex={-1}>
            <Outlet />
          </main>
        </div>
      </div>
      <div id="operation-live-region" className="sr-only" role="status" aria-live="polite" aria-atomic="true" />
    </>
  );
}
