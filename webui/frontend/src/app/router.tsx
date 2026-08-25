import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";
import { createBrowserRouter, Navigate, useLocation } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { NotFoundPage } from "../pages/NotFoundPage";
import { FLAT_ROUTE_REDIRECTS } from "./navigation";

function lazyPage<T extends Record<string, unknown>, K extends keyof T>(loader: () => Promise<T>, name: K) {
  return lazy(async () => ({ default: (await loader())[name] as ComponentType }));
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

const flatRedirects = Object.entries(FLAT_ROUTE_REDIRECTS).map(([path, to]) => ({ path: path.slice(1), element: <RedirectWithQuery to={to} /> }));

export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <AppShell />,
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
