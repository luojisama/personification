import { API_BASE } from "../api/client";
import type { RuntimeEvent } from "../api/types";

export const EVENT_CURSOR_KEY = "personification.console.last-event-id";

export interface SseFrame {
  id?: string;
  event: string;
  data: string;
}

export interface RuntimeEventClientOptions {
  onEvent: (event: RuntimeEvent) => void;
  onResync: (latestId: number) => void;
  onState: (state: "connecting" | "open" | "retrying" | "closed") => void;
  fetcher?: typeof fetch;
  storage?: Pick<Storage, "getItem" | "setItem">;
}

export function parseSseBlock(block: string): SseFrame | null {
  const frame: SseFrame = { event: "message", data: "" };
  const data: string[] = [];
  let meaningful = false;
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(":")) continue;
    meaningful = true;
    const separator = rawLine.indexOf(":");
    const field = separator >= 0 ? rawLine.slice(0, separator) : rawLine;
    const value = separator >= 0 ? rawLine.slice(separator + 1).replace(/^ /, "") : "";
    if (field === "id") frame.id = value;
    if (field === "event") frame.event = value || "message";
    if (field === "data") data.push(value);
  }
  if (!meaningful) return null;
  frame.data = data.join("\n");
  return frame;
}

function csrfHeader(): string {
  return document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith("personification_webui_csrf="))
    ?.split("=")
    .slice(1)
    .join("=") ?? "";
}

export class RuntimeEventClient {
  private readonly options: RuntimeEventClientOptions;
  private readonly controller = new AbortController();
  private cursor = 0;
  private stopped = false;

  constructor(options: RuntimeEventClientOptions) {
    this.options = options;
    const raw = options.storage?.getItem(EVENT_CURSOR_KEY) ?? window.sessionStorage.getItem(EVENT_CURSOR_KEY);
    const parsed = Number(raw);
    this.cursor = Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
  }

  private persistCursor(): void {
    const storage = this.options.storage ?? window.sessionStorage;
    try {
      storage.setItem(EVENT_CURSOR_KEY, String(this.cursor));
    } catch {
      // 游标只影响断线补发；存储不可用时仍保持当前连接。
    }
  }

  stop(): void {
    this.stopped = true;
    this.controller.abort();
    this.options.onState("closed");
  }

  async start(): Promise<void> {
    let backoff = 1_000;
    while (!this.stopped) {
      this.options.onState(this.cursor > 0 ? "retrying" : "connecting");
      try {
        await this.connectOnce();
        backoff = 1_000;
      } catch (error) {
        if (this.stopped || (error instanceof DOMException && error.name === "AbortError")) break;
      }
      if (this.stopped) break;
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, backoff);
        this.controller.signal.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timer);
            resolve();
          },
          { once: true },
        );
      });
      backoff = Math.min(backoff * 2, 15_000);
    }
  }

  private async connectOnce(): Promise<void> {
    const headers = new Headers({ Accept: "text/event-stream" });
    if (this.cursor > 0) headers.set("Last-Event-ID", String(this.cursor));
    const csrf = csrfHeader();
    if (csrf) headers.set("X-Personification-CSRF", decodeURIComponent(csrf));
    const response = await (this.options.fetcher ?? fetch)(`${API_BASE}/events`, {
      method: "GET",
      headers,
      credentials: "include",
      signal: this.controller.signal,
    });
    if (!response.ok || !response.body) throw new Error(`SSE 不可用（sse_http_${response.status}）`);
    this.options.onState("open");

    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    while (!this.stopped) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const frame = parseSseBlock(block);
        if (frame) this.handleFrame(frame);
      }
    }
  }

  private handleFrame(frame: SseFrame): void {
    let payload: unknown = {};
    try {
      payload = frame.data ? JSON.parse(frame.data) : {};
    } catch {
      return;
    }
    const record = typeof payload === "object" && payload !== null ? (payload as Record<string, unknown>) : {};
    if (frame.event === "resync_required") {
      const latest = Number(record.latest_id ?? 0);
      this.cursor = Number.isSafeInteger(latest) && latest >= 0 ? latest : 0;
      this.persistCursor();
      this.options.onResync(this.cursor);
      return;
    }
    const id = Number(record.id ?? frame.id ?? 0);
    if (!Number.isSafeInteger(id) || id <= 0) return;
    this.cursor = Math.max(this.cursor, id);
    this.persistCursor();
    this.options.onEvent({
      id,
      ts:
        typeof record.ts === "string" || typeof record.ts === "number"
          ? record.ts
          : new Date().toISOString(),
      topic: typeof record.topic === "string" ? record.topic : frame.event,
      trace_id: typeof record.trace_id === "string" ? record.trace_id : undefined,
      payload:
        typeof record.payload === "object" && record.payload !== null
          ? (record.payload as Record<string, unknown>)
          : {},
    });
  }
}
