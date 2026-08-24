import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { AgentStatusPage } from "../pages/AgentStatusPage";
import { ConfigCenterPage } from "../pages/ConfigCenterPage";
import { FeatureWorkbenchPage } from "../pages/FeatureWorkbenchPage";
import { FunctionalTestsPage } from "../pages/FunctionalTestsPage";
import { ManagementDataPage } from "../pages/ManagementDataPage";
import { ModelTestsPage } from "../pages/ModelTestsPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { OverviewPage } from "../pages/OverviewPage";
import { QzoneCapabilitiesPage } from "../pages/QzoneCapabilitiesPage";
import { RecoveryPage } from "../pages/RecoveryPage";
import { RouteCapabilitiesPage } from "../pages/RouteCapabilitiesPage";
import { RuntimeCatalogPage } from "../pages/RuntimeCatalogPage";
import { SettingsPage } from "../pages/SettingsPage";
import { SystemDiagnosticsPage } from "../pages/SystemDiagnosticsPage";
import { TokenStatisticsPage } from "../pages/TokenStatisticsPage";
import { TracesPage } from "../pages/TracesPage";

export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <AppShell />,
      children: [
        { index: true, element: <OverviewPage /> },
        { path: "agent-status", element: <AgentStatusPage /> },
        { path: "tokens", element: <TokenStatisticsPage /> },
        { path: "health", element: <FunctionalTestsPage /> },
        { path: "model-tests", element: <ModelTestsPage /> },
        { path: "routes", element: <RouteCapabilitiesPage /> },
        { path: "proactive", element: <FeatureWorkbenchPage feature="proactive" /> },
        { path: "traces", element: <TracesPage /> },
        { path: "traces/:traceId", element: <TracesPage /> },
        { path: "recovery", element: <RecoveryPage /> },
        { path: "qzone", element: <QzoneCapabilitiesPage /> },
        { path: "personas", element: <ManagementDataPage dataset="personas" /> },
        { path: "groups", element: <ManagementDataPage dataset="groups" /> },
        { path: "group-switches", element: <FeatureWorkbenchPage feature="group-switches" /> },
        { path: "memories", element: <RuntimeCatalogPage dataset="memories" /> },
        { path: "memory-palace", element: <FeatureWorkbenchPage feature="memory-palace" /> },
        { path: "stickers", element: <ManagementDataPage dataset="stickers" /> },
        { path: "persona-preview", element: <FeatureWorkbenchPage feature="persona-preview" /> },
        { path: "persona-builder", element: <FeatureWorkbenchPage feature="persona-builder" /> },
        { path: "skills", element: <RuntimeCatalogPage dataset="skills" /> },
        { path: "mcp", element: <RuntimeCatalogPage dataset="mcp" /> },
        { path: "tool-creator", element: <RuntimeCatalogPage dataset="tool-tasks" /> },
        { path: "plugin-knowledge", element: <RuntimeCatalogPage dataset="plugin-knowledge" /> },
        { path: "plugins", element: <FeatureWorkbenchPage feature="plugins" /> },
        { path: "config", element: <ConfigCenterPage /> },
        { path: "user-policies", element: <FeatureWorkbenchPage feature="user-policies" /> },
        { path: "outbound", element: <FeatureWorkbenchPage feature="outbound" /> },
        { path: "data-transfer", element: <FeatureWorkbenchPage feature="data-transfer" /> },
        { path: "audit", element: <FeatureWorkbenchPage feature="audit" /> },
        { path: "logs", element: <RuntimeCatalogPage dataset="logs" /> },
        { path: "qq", element: <FeatureWorkbenchPage feature="qq" /> },
        { path: "devices", element: <FeatureWorkbenchPage feature="devices" /> },
        { path: "systems", element: <SystemDiagnosticsPage /> },
        { path: "settings", element: <SettingsPage /> },
        { path: "data", element: <ManagementDataPage /> },
        { path: "catalog", element: <RuntimeCatalogPage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/personification/frontend" },
);
