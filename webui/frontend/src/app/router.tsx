import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { NotFoundPage } from "../pages/NotFoundPage";
import { OverviewPage } from "../pages/OverviewPage";
import { ManagementDataPage } from "../pages/ManagementDataPage";
import { RecoveryPage } from "../pages/RecoveryPage";
import { RouteCapabilitiesPage } from "../pages/RouteCapabilitiesPage";
import { SettingsPage } from "../pages/SettingsPage";
import { TracesPage } from "../pages/TracesPage";
import { SystemDiagnosticsPage } from "../pages/SystemDiagnosticsPage";

export const router = createBrowserRouter(
  [
    {
      path: "/",
      element: <AppShell />,
      children: [
        { index: true, element: <OverviewPage /> },
        { path: "routes", element: <RouteCapabilitiesPage /> },
        { path: "traces", element: <TracesPage /> },
        { path: "traces/:traceId", element: <TracesPage /> },
        { path: "recovery", element: <RecoveryPage /> },
        { path: "data", element: <ManagementDataPage /> },
        { path: "systems", element: <SystemDiagnosticsPage /> },
        { path: "settings", element: <SettingsPage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/personification/frontend" },
);
