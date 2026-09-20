import { inject, ref, shallowRef, type App, type InjectionKey, type Ref, type ShallowRef } from "vue";
import type { QueryClient } from "@tanstack/vue-query";

import { onAuthenticationInvalid } from "@/api/client";
import type { RuntimeEvent } from "@/api/types";
import { RuntimeEventClient, type RuntimeEventClientOptions } from "@/realtime/sse";

export type ConnectionState = "connecting" | "open" | "retrying" | "closed";

export interface RuntimeEventClientLike {
  start(): Promise<void>;
  stop(): void;
}

export type RuntimeEventClientFactory = (
  options: RuntimeEventClientOptions,
) => RuntimeEventClientLike;

export interface RuntimeEventsManager {
  events: ShallowRef<RuntimeEvent[]>;
  state: Ref<ConnectionState>;
  resyncCount: Ref<number>;
  client: ShallowRef<RuntimeEventClientLike | null>;
  start(): void;
  stop(): void;
}

export const TOPIC_QUERY_MAP: Record<string, readonly string[]> = {
  "turn.started": ["overview", "traces"],
  "turn.stage": ["overview", "traces"],
  "turn.finished": ["overview", "traces"],
  "provider.status_changed": ["overview", "route-capabilities"],
  "route_probe.updated": ["route-capabilities", "route-probe-history"],
  "recovery.updated": ["overview", "recovery"],
  "log.appended": ["overview"],
  "qzone.capability_changed": ["overview"],
  "test_run.updated": ["functional-health"],
  "admin_index.updated": ["management-data", "group-switches"],
  "group_switch.updated": ["management-data", "group-switches"],
  "plugin_update.updated": ["plugin-update-status", "plugin-update-history"],
};

export const RUNTIME_EVENTS_KEY: InjectionKey<RuntimeEventsManager> = Symbol(
  "personification:runtime-events",
);

export function createRuntimeEventsManager(
  queryClient: QueryClient,
  clientFactory: RuntimeEventClientFactory = (options) => new RuntimeEventClient(options),
): RuntimeEventsManager {
  const events = shallowRef<RuntimeEvent[]>([]);
  const state = ref<ConnectionState>("connecting");
  const resyncCount = ref(0);
  const client = shallowRef<RuntimeEventClientLike | null>(null);
  let generation = 0;

  function handleEvent(event: RuntimeEvent): void {
    events.value = [...events.value, event].slice(-500);
    for (const key of TOPIC_QUERY_MAP[event.topic] ?? []) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  }

  function handleResync(_latestId: number): void {
    events.value = [];
    resyncCount.value += 1;
    void queryClient.invalidateQueries();
  }

  function start(): void {
    if (client.value) return;
    const currentGeneration = ++generation;
    const active = () => generation === currentGeneration && client.value === instance;
    const instance = clientFactory({
      onEvent: (event) => { if (active()) handleEvent(event); },
      onResync: (latestId) => { if (active()) handleResync(latestId); },
      onState: (nextState) => {
        if (active()) state.value = nextState;
      },
      onUnauthorized: () => stop(),
    });
    client.value = instance;
    void instance.start();
  }

  function stop(): void {
    generation += 1;
    client.value?.stop();
    client.value = null;
    events.value = [];
    resyncCount.value = 0;
    try { window.sessionStorage.removeItem("personification.console.last-event-id"); } catch { /* 忽略不可用存储 */ }
    state.value = "closed";
  }

  onAuthenticationInvalid(() => stop());

  return { events, state, resyncCount, client, start, stop };
}

export function provideRuntimeEvents(app: App, manager: RuntimeEventsManager): void {
  app.provide(RUNTIME_EVENTS_KEY, manager);
}

export function useRuntimeEvents(): RuntimeEventsManager {
  const manager = inject(RUNTIME_EVENTS_KEY);
  if (!manager) {
    throw new Error("实时事件上下文尚未初始化");
  }
  return manager;
}
