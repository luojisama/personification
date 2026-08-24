import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { NotFoundPage } from "../pages/NotFoundPage";
import { OverviewPage } from "../pages/OverviewPage";
import { RecoveryPage } from "../pages/RecoveryPage";
import { RouteCapabilitiesPage } from "../pages/RouteCapabilitiesPage";
import { SettingsPage } from "../pages/SettingsPage";
import { TracesPage } from "../pages/TracesPage";

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
        { path: "settings", element: <SettingsPage /> },
        { path: "*", element: <NotFoundPage /> },
      ],
    },
  ],
  { basename: "/personification/frontend" },
);
