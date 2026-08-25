import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { BotProvider, useBot } from "../app/BotContext";
import { NAVIGATION_GROUPS, NAVIGATION_LEAVES, NAVIGATION_ITEMS, navigationContext } from "../app/navigation";
import { useRuntimeEvents } from "../realtime/RuntimeEventsProvider";
import { THEME_META, THEMES, useTheme } from "../theme/theme";
import { Icon } from "./Icon";
import { IdentityAvatar } from "./IdentityAvatar";
import { StateBadge } from "./StateBadge";

function nextTheme(current: (typeof THEMES)[number]) {
  return THEMES[(THEMES.indexOf(current) + 1) % THEMES.length] ?? "minimal";
}

export function AppShell() {
  return <BotProvider><ShellContent /></BotProvider>;
}

function ShellContent() {
  const [theme, setTheme] = useTheme();
  const realtime = useRuntimeEvents();
  const location = useLocation();
  const navigate = useNavigate();
  const { bots, selectedBot, botId, setBotId } = useBot();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(() => localStorage.getItem("personification.nav.collapsed") === "1");
  const [search, setSearch] = useState("");
  const context = navigationContext(location.pathname);
  const targetTheme = nextTheme(theme);
  const results = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase("zh-CN");
    if (!needle) return [];
    return [...NAVIGATION_ITEMS, ...NAVIGATION_LEAVES]
      .filter((item) => [item.label, ...item.aliases].join(" ").toLocaleLowerCase("zh-CN").includes(needle))
      .slice(0, 10);
  }, [search]);
  const visit = (path: string) => {
    setSearch("");
    setDrawerOpen(false);
    void navigate(path);
  };

  useEffect(() => {
    if (context?.leaf.path) localStorage.setItem(`personification.nav.last.${context.group.id}`, context.leaf.path);
  }, [context?.group.id, context?.leaf.path]);

  const visitGroup = (groupId: string, fallback: string | null) => {
    const remembered = localStorage.getItem(`personification.nav.last.${groupId}`);
    visit(remembered || fallback || "/runtime/overview/summary");
  };

  const toggleRail = () => {
    setRailCollapsed((value) => {
      localStorage.setItem("personification.nav.collapsed", value ? "0" : "1");
      return !value;
    });
  };

  return (
    <>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <button className="mobile-nav-trigger" type="button" aria-expanded={drawerOpen} aria-controls="admin-navigation" onClick={() => setDrawerOpen((value) => !value)}>
        <Icon name={drawerOpen ? "close" : "data"} /> 菜单
      </button>
      {drawerOpen && <button className="drawer-scrim" type="button" aria-label="关闭导航" onClick={() => setDrawerOpen(false)} />}
      <div className={`app-frame${railCollapsed ? " rail-collapsed" : ""}`}>
        <aside id="admin-navigation" className={`evidence-rail${drawerOpen ? " is-open" : ""}`} aria-label="管理台一级导航">
          <div className="brand-plate">
            <IdentityAvatar src={selectedBot?.avatar_url} label={selectedBot?.nickname || "P/F"} size="large" square />
            <div className="brand-copy rail-expandable">
              <strong>{selectedBot?.nickname || "拟人插件"}</strong>
              <small>{selectedBot?.bot_id ? `QQ ${selectedBot.bot_id}` : "事件取证台"}</small>
            </div>
            {bots.length > 1 && !railCollapsed && (
              <select className="bot-selector" aria-label="选择 Bot" value={botId} onChange={(event) => setBotId(event.target.value)}>
                {bots.map((bot) => <option key={bot.bot_id} value={bot.bot_id}>{bot.nickname} · {bot.bot_id}</option>)}
              </select>
            )}
          </div>
          <div className="global-page-search rail-expandable">
            <Icon name="search" />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索页面或功能" aria-label="搜索页面或功能" />
            {results.length > 0 && (
              <div className="page-search-results" role="listbox" aria-label="页面搜索结果">
                {results.map((item) => <button key={item.id} type="button" role="option" onClick={() => visit(item.path ?? "/runtime/overview/summary")}><Icon name={item.icon} /><span>{item.label}</span><small>{item.aliases[0]}</small></button>)}
              </div>
            )}
          </div>
          <nav className="primary-nav" aria-label="一级分类">
            {NAVIGATION_GROUPS.map((group) => (
              <button
                className={context?.group.id === group.id ? "active" : ""}
                key={group.id}
                type="button"
                aria-current={context?.group.id === group.id ? "page" : undefined}
                title={railCollapsed ? group.label : undefined}
                onClick={() => visitGroup(group.id, group.path)}
              >
                <Icon name={group.icon} />
                <span className="nav-label rail-expandable">{group.label}</span>
              </button>
            ))}
          </nav>
          <div className="rail-foot">
            <button className="rail-collapse" type="button" onClick={toggleRail} title={railCollapsed ? "展开一级导航" : "收起一级导航"}>
              <Icon name="chevron" /><span className="rail-expandable">收起导航</span>
            </button>
            <a className="legacy-entry rail-expandable" href="/personification/">进入旧版管理台</a>
            <button className="theme-cycle" type="button" onClick={() => setTheme(targetTheme)} aria-label={`切换到 ${THEME_META[targetTheme].name} 主题`} title={`切换到 ${THEME_META[targetTheme].name} 主题`}>
              <span className="theme-swatch" aria-hidden="true" />
              <span className="rail-expandable">{THEME_META[theme].name}</span>
            </button>
            <div className="rail-expandable"><StateBadge tone={realtime.state === "open" ? "ok" : realtime.state === "closed" ? "error" : "running"} raw={realtime.state}>
              {realtime.state === "open" ? "实时事件已连接" : realtime.state === "closed" ? "实时事件已关闭" : "正在连接事件流"}
            </StateBadge></div>
          </div>
        </aside>
        <div className="workbench">
          <header className="top-status-line">
            <span><Icon name="signal" /> 实时事件 {realtime.events.length}/500</span>
            <span>{selectedBot?.online ? "Bot 在线" : "Bot 未连接"}{selectedBot?.bot_id ? ` · ${selectedBot.bot_id}` : ""}</span>
            {realtime.resyncCount > 0 && <span>REST 重同步 {realtime.resyncCount} 次</span>}
            <code>{location.pathname}</code>
          </header>
          {context && (
            <div className="workspace-navigation">
              <nav className="secondary-navigation" aria-label={`${context.group.label}二级导航`}>
                {context.group.children.map((item) => <NavLink key={item.id} to={item.path ?? "#"} className={context.page.id === item.id ? "active" : ""}>{item.label}</NavLink>)}
              </nav>
              <nav className="tertiary-navigation" aria-label={`${context.page.label}三级导航`}>
                <span className="workspace-breadcrumb">{context.group.label} / {context.page.label}</span>
                {context.page.children.map((item) => <NavLink key={item.id} to={item.path ?? "#"} className={context.leaf.id === item.id ? "active" : ""} aria-current={context.leaf.id === item.id ? "page" : undefined}>{item.label}</NavLink>)}
              </nav>
            </div>
          )}
          <main id="main-content" className="main-workspace" tabIndex={-1}>
            <Outlet />
          </main>
        </div>
      </div>
      <div id="operation-live-region" className="sr-only" role="status" aria-live="polite" aria-atomic="true" />
    </>
  );
}
