import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { RuntimeEvent } from "../api/types";
import { RuntimeEventClient } from "./sse";

type ConnectionState = "connecting" | "open" | "retrying" | "closed";

interface RuntimeEventsValue {
  events: RuntimeEvent[];
  state: ConnectionState;
  resyncCount: number;
}

const RuntimeEventsContext = createContext<RuntimeEventsValue>({
  events: [],
  state: "connecting",
  resyncCount: 0,
});

const TOPIC_QUERY_MAP: Record<string, string[]> = {
  "turn.started": ["overview", "traces"],
  "turn.stage": ["overview", "traces"],
  "turn.finished": ["overview", "traces"],
  "provider.status_changed": ["overview", "route-capabilities"],
  "recovery.updated": ["overview", "recovery"],
  "log.appended": ["overview"],
  "qzone.capability_changed": ["overview"],
  "test_run.updated": ["functional-health"],
};

export function RuntimeEventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [state, setState] = useState<ConnectionState>("connecting");
  const [resyncCount, setResyncCount] = useState(0);

  useEffect(() => {
    const client = new RuntimeEventClient({
      onEvent: (event) => {
        setEvents((current) => [...current, event].slice(-500));
        for (const key of TOPIC_QUERY_MAP[event.topic] ?? []) {
          void queryClient.invalidateQueries({ queryKey: [key] });
        }
      },
      onResync: () => {
        setEvents([]);
        setResyncCount((count) => count + 1);
        void queryClient.invalidateQueries();
      },
      onState: setState,
    });
    void client.start();
    return () => client.stop();
  }, [queryClient]);

  const value = useMemo(() => ({ events, state, resyncCount }), [events, state, resyncCount]);
  return <RuntimeEventsContext.Provider value={value}>{children}</RuntimeEventsContext.Provider>;
}

export function useRuntimeEvents(): RuntimeEventsValue {
  return useContext(RuntimeEventsContext);
}
