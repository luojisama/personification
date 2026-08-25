import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";
import { createBrowserRouter, Navigate, useLocation, useParams, useRouteError } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { NotFoundPage } from "../pages/NotFoundPage";
import { FLAT_ROUTE_REDIRECTS } from "./navigation";

function lazyPage<T extends Record<string, unknown>, K extends keyof T>(loader: () => Promise<T>, name: K) {
  return lazy(async () => {
    try {
      return { default: (await loader())[name] as ComponentType };
    } catch (error) {
      // 公网管理台可能偶发丢失单个静态分块；只做一次短退避读取，
      // 仍失败时交给中文错误边界，不进行无限刷新。
      await new Promise((resolve) => window.setTimeout(resolve, 350));
      try {
        return { default: (await loader())[name] as ComponentType };
      } catch {
        throw error;
      }
    }
  });
}

const OverviewPage = lazyPage(() => import("../pages/OverviewPage"), "OverviewPage");
const AgentStatusPage = lazyPage(() => import("../pages/AgentStatusPage"), "AgentStatusPage");
const TokenStatisticsPage = lazyPage(() => import("../pages/TokenStatisticsPage"), "TokenStatisticsPage");
const FunctionalTestsPage = lazyPage(() => import("../pages/FunctionalTestsPage"), "FunctionalTestsPage");
const ModelTestsPage = lazyPage(() => import("../pages/ModelTestsPage"), "ModelTestsPage");
const RouteCapabilitiesPage = lazyPage(() => import("../pages/RouteCapabilitiesPage"), "RouteCapabilitiesPage");
const ProactiveDiagnosticsPage = lazyPage(() => import("../pages/ProactiveDiagnosticsPage"), "ProactiveDiagnosticsPage");
const TracesPage = lazyPage(() => import("../pages/TracesPage"), "TracesPage");
const RecoveryPage = lazyPage(() => import("../pages/RecoveryPage"), "RecoveryPage");
const QzoneCapabilitiesPage = lazyPage(() => import("../pages/QzoneCapabilitiesPage"), "QzoneCapabilitiesPage");
const PersonasPage = lazyPage(() => import("../pages/IndexedDirectoryPages"), "PersonasPage");
const GroupsPage = lazyPage(() => import("../pages/IndexedDirectoryPages"), "GroupsPage");
const StickersPage = lazyPage(() => import("../pages/IndexedDirectoryPages"), "StickersPage");
const GroupSwitchesPage = lazyPage(() => import("../pages/GroupSwitchesPage"), "GroupSwitchesPage");
const MemoryManagementPage = lazyPage(() => import("../pages/MemoryAndLogsPages"), "MemoryManagementPage");
const PluginLogsPage = lazyPage(() => import("../pages/MemoryAndLogsPages"), "PluginLogsPage");
const MemoryPalacePage = lazyPage(() => import("../pages/PersonaFeaturePages"), "MemoryPalacePage");
const PersonaPreviewPage = lazyPage(() => import("../pages/PersonaFeaturePages"), "PersonaPreviewPage");
const PersonaBuilderPage = lazyPage(() => import("../pages/PersonaFeaturePages"), "PersonaBuilderPage");
const SkillsPage = lazyPage(() => import("../pages/CapabilityBusinessPages"), "SkillsPage");
const McpManagementPage = lazyPage(() => import("../pages/CapabilityBusinessPages"), "McpManagementPage");
const ToolCreatorPage = lazyPage(() => import("../pages/CapabilityBusinessPages"), "ToolCreatorPage");
const PluginKnowledgePage = lazyPage(() => import("../pages/CapabilityBusinessPages"), "PluginKnowledgePage");
const PluginManagementPage = lazyPage(() => import("../pages/CapabilityBusinessPages"), "PluginManagementPage");
const ConfigCenterPage = lazyPage(() => import("../pages/ConfigCenterPage"), "ConfigCenterPage");
const UserPoliciesPage = lazyPage(() => import("../pages/OperationsBusinessPages"), "UserPoliciesPage");
const OutboundMessagesPage = lazyPage(() => import("../pages/OperationsBusinessPages"), "OutboundMessagesPage");
const DataTransferPage = lazyPage(() => import("../pages/OperationsBusinessPages"), "DataTransferPage");
const AuditLogPage = lazyPage(() => import("../pages/OperationsBusinessPages"), "AuditLogPage");
const QqManagementPage = lazyPage(() => import("../pages/OperationsBusinessPages"), "QqManagementPage");
const DeviceManagementPage = lazyPage(() => import("../pages/OperationsBusinessPages"), "DeviceManagementPage");
const SystemDiagnosticsPage = lazyPage(() => import("../pages/SystemDiagnosticsPage"), "SystemDiagnosticsPage");
const SettingsPage = lazyPage(() => import("../pages/SettingsPage"), "SettingsPage");

function pageElement(Component: LazyExoticComponent<ComponentType>) {
  return <Suspense fallback={<div className="route-loading" role="status">正在加载业务页面…</div>}><Component /></Suspense>;
}

function RedirectWithQuery({ to }: { to: string }) {
  const location = useLocation();
  return <Navigate replace to={`${to}${location.search}`} />;
}

function LegacyTraceRedirect() {
  const location = useLocation();
  const { traceId = "" } = useParams();
  return <Navigate replace to={`/runtime/traces/timeline/${encodeURIComponent(traceId)}${location.search}`} />;
}

function RouteErrorPage() {
  const error = useRouteError();
  const message = error instanceof Error ? error.message : String(error ?? "");
  const chunkFailed = /dynamically imported module|failed to fetch/i.test(message);
  return (
    <main className="main-workspace" id="main-content">
      <div className="page-stack">
        <header className="page-heading">
          <div className="page-title-block">
            <span className="page-kicker">ADMIN / ERROR</span>
            <h1>{chunkFailed ? "页面资源加载失败" : "页面暂时无法显示"}</h1>
            <p>{chunkFailed ? "公网连接未能取得当前页面分块，可安全刷新后重试。" : "页面渲染遇到异常，写操作没有因此自动重试。"}</p>
          </div>
          <div className="page-actions">
            <button className="button button-primary" type="button" onClick={() => window.location.reload()}>刷新当前页面</button>
          </div>
        </header>
        <div className="empty-state">
          <span className="empty-mark" aria-hidden="true">!</span>
          <p>若刷新后仍失败，请核对服务状态和浏览器网络。</p>
          <code>{chunkFailed ? "frontend_chunk_load_failed" : "frontend_route_render_failed"}</code>
        </div>
      </div>
    </main>
  );
}

const flatRedirects = Object.entries(FLAT_ROUTE_REDIRECTS).map(([path, to]) => ({ path: path.slice(1), element: <RedirectWithQuery to={to} /> }));

export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <AppShell />,
      errorElement: <RouteErrorPage />,
      children: [
        { index: true, element: <Navigate replace to="/runtime/overview/summary" /> },
        { path: "runtime/overview/summary", element: pageElement(OverviewPage) },
        { path: "runtime/agent/:section", element: pageElement(AgentStatusPage) },
        { path: "runtime/tokens/:window", element: pageElement(TokenStatisticsPage) },
        { path: "runtime/health/:section", element: pageElement(FunctionalTestsPage) },
        { path: "runtime/model-tests/:section", element: pageElement(ModelTestsPage) },
        { path: "runtime/routes/:section", element: pageElement(RouteCapabilitiesPage) },
        { path: "runtime/proactive/:section", element: pageElement(ProactiveDiagnosticsPage) },
        { path: "runtime/traces/index", element: pageElement(TracesPage) },
        { path: "runtime/traces/timeline", element: pageElement(TracesPage) },
        { path: "runtime/traces/timeline/:traceId", element: pageElement(TracesPage) },
        { path: "traces/:traceId", element: <LegacyTraceRedirect /> },
        { path: "runtime/recovery/:section", element: pageElement(RecoveryPage) },
        { path: "runtime/qzone/:section", element: pageElement(QzoneCapabilitiesPage) },
        { path: "persona/personas/:section", element: pageElement(PersonasPage) },
        { path: "persona/groups/:section", element: pageElement(GroupsPage) },
        { path: "persona/group-switches/list", element: pageElement(GroupSwitchesPage) },
        { path: "persona/memories/:section", element: pageElement(MemoryManagementPage) },
        { path: "persona/memory-palace/:section", element: pageElement(MemoryPalacePage) },
        { path: "persona/stickers/:section", element: pageElement(StickersPage) },
        { path: "persona/persona-preview/:section", element: pageElement(PersonaPreviewPage) },
        { path: "persona/persona-builder/:section", element: pageElement(PersonaBuilderPage) },
        { path: "capability/skills/:section", element: pageElement(SkillsPage) },
        { path: "capability/mcp/:section", element: pageElement(McpManagementPage) },
        { path: "capability/tool-creator/:section", element: pageElement(ToolCreatorPage) },
        { path: "capability/plugin-knowledge/:section", element: pageElement(PluginKnowledgePage) },
        { path: "capability/plugins/:section", element: pageElement(PluginManagementPage) },
        { path: "operations/config/:section", element: pageElement(ConfigCenterPage) },
        { path: "operations/user-policies/:section", element: pageElement(UserPoliciesPage) },
        { path: "operations/outbound/:section", element: pageElement(OutboundMessagesPage) },
        { path: "operations/data-transfer/:section", element: pageElement(DataTransferPage) },
        { path: "operations/audit/:section", element: pageElement(AuditLogPage) },
        { path: "operations/logs/:section", element: pageElement(PluginLogsPage) },
        { path: "operations/qq/:section", element: pageElement(QqManagementPage) },
        { path: "operations/devices/:section", element: pageElement(DeviceManagementPage) },
        { path: "operations/systems/:section", element: pageElement(SystemDiagnosticsPage) },
        { path: "operations/settings/:section", element: pageElement(SettingsPage) },
        ...flatRedirects,
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/personification/frontend" },
);
