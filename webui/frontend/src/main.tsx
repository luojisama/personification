import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { queryClient } from "./app/queryClient";
import { router } from "./app/router";
import { RuntimeEventsProvider } from "./realtime/RuntimeEventsProvider";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/pages.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("缺少前端挂载节点（frontend_root_missing）");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RuntimeEventsProvider>
        <RouterProvider router={router} />
      </RuntimeEventsProvider>
    </QueryClientProvider>
  </StrictMode>,
);
