import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/vue-query";

import type { RuntimeEvent } from "@/api/types";
import type { RuntimeEventClientOptions } from "@/realtime/sse";
import { createRuntimeEventsManager, type RuntimeEventClientLike } from "./runtimeEvents";

class FakeClient implements RuntimeEventClientLike {
  startCalls = 0;
  stopCalls = 0;

  constructor(readonly options: RuntimeEventClientOptions) {}

  async start(): Promise<void> {
    this.startCalls += 1;
    this.options.onState("open");
  }

  stop(): void {
    this.stopCalls += 1;
    this.options.onState("closed");
  }
}

function event(id: number, topic = "turn.started"): RuntimeEvent {
  return { id, ts: id, topic, payload: { id } };
}

describe("Runtime Events 管理器", () => {
  it("单例启动、定向失效、重同步和停止均可观测", () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    let fake: FakeClient | null = null;
    const manager = createRuntimeEventsManager(queryClient, (options) => {
      fake = new FakeClient(options);
      return fake;
    });

    manager.start();
    manager.start();
    expect(fake).not.toBeNull();
    expect((fake as FakeClient | null)?.startCalls).toBe(1);

    fake!.options.onEvent(event(1));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["overview"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["traces"] });

    fake!.options.onResync(100);
    expect(manager.events.value).toEqual([]);
    expect(manager.resyncCount.value).toBe(1);
    expect(invalidate).toHaveBeenCalledWith();

    manager.stop();
    expect(fake!.stopCalls).toBe(1);
    expect(manager.client.value).toBeNull();
    expect(manager.state.value).toBe("closed");
  });

  it("仅保留最新五百条事件且不为未知主题失效查询", () => {
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries").mockResolvedValue();
    let fake: FakeClient | null = null;
    const manager = createRuntimeEventsManager(queryClient, (options) => {
      fake = new FakeClient(options);
      return fake;
    });
    manager.start();
    for (let id = 1; id <= 550; id += 1) fake!.options.onEvent(event(id, "unknown.topic"));
    expect(manager.events.value).toHaveLength(500);
    expect(manager.events.value[0]?.id).toBe(51);
    expect(manager.events.value.at(-1)?.id).toBe(550);
    expect(invalidate).not.toHaveBeenCalled();
  });
});
