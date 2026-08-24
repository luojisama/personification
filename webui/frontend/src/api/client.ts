const API_BASE = "/personification/api/v2";
const LEGACY_API_BASE = "/personification/api";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readCookie(name: string): string {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const entry of document.cookie.split(";")) {
    const value = entry.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return "";
}

function detailCode(detail: unknown): string {
  if (isRecord(detail) && typeof detail.code === "string") return detail.code;
  return "api_request_failed";
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly phase: string;
  readonly detail: unknown;
  readonly traceId: string;
  readonly operationId: string;
  readonly retryable: boolean;
  readonly outcomeUnknown: boolean;

  constructor(status: number, detail: unknown) {
    const record = isRecord(detail) ? detail : {};
    const nested = isRecord(record.detail) ? record.detail : record;
    super(`API 请求失败（${detailCode(nested)}）`);
    this.name = "ApiError";
    this.status = status;
    this.code = detailCode(nested);
    this.phase = typeof nested.phase === "string" ? nested.phase : "http_request";
    this.detail = detail;
    this.traceId = typeof nested.trace_id === "string" ? nested.trace_id : "";
    this.operationId = typeof nested.operation_id === "string" ? nested.operation_id : "";
    this.retryable = nested.retryable === true;
    this.outcomeUnknown = nested.outcome_unknown === true;
  }
}

function buildUrlAt(base: string, path: string, query?: Record<string, string | number | boolean | null | undefined>): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const url = new URL(`${base}${normalized}`, window.location.origin);
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });
  return `${url.pathname}${url.search}`;
}

function buildUrl(path: string, query?: Record<string, string | number | boolean | null | undefined>): string {
  return buildUrlAt(API_BASE, path, query);
}

export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
}

async function apiRequestAt<T>(base: string, path: string, options: ApiRequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (!SAFE_METHODS.has(method)) {
    const csrf = readCookie("personification_webui_csrf");
    if (csrf) headers.set("X-Personification-CSRF", csrf);
  }

  const response = await fetch(buildUrlAt(base, path, options.query), {
    ...options,
    method,
    credentials: "include",
    headers,
    body:
      options.body === undefined
        ? undefined
        : options.body instanceof FormData
          ? options.body
          : JSON.stringify(options.body),
  });

  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) throw new ApiError(response.status, payload);
  return payload as T;
}

export async function rawApiRequest<T>(base: string, path: string, body: BodyInit, headers: Record<string, string>, signal?: AbortSignal): Promise<T> {
  const requestHeaders = new Headers(headers);
  const csrf = readCookie("personification_webui_csrf");
  if (csrf) requestHeaders.set("X-Personification-CSRF", csrf);
  const response = await fetch(buildUrlAt(base, path), { method: "POST", body, headers: requestHeaders, credentials: "include", signal });
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload as T;
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  return apiRequestAt(API_BASE, path, options);
}

export const api = {
  get<T>(path: string, query?: ApiRequestOptions["query"], signal?: AbortSignal): Promise<T> {
    return apiRequest<T>(path, { query, signal });
  },
  post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return apiRequest<T>(path, { method: "POST", body, signal });
  },
  patch<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return apiRequest<T>(path, { method: "PATCH", body, signal });
  },
  upload<T>(path: string, file: File, signal?: AbortSignal): Promise<T> {
    return rawApiRequest<T>(API_BASE, path, file, {
      "Content-Type": file.type || "application/octet-stream",
      "X-Personification-Video-Filename": file.name,
    }, signal);
  },
};

export const legacyApi = {
  get<T>(path: string, query?: ApiRequestOptions["query"], signal?: AbortSignal): Promise<T> {
    return apiRequestAt<T>(LEGACY_API_BASE, path, { query, signal });
  },
  post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return apiRequestAt<T>(LEGACY_API_BASE, path, { method: "POST", body, signal });
  },
  put<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return apiRequestAt<T>(LEGACY_API_BASE, path, { method: "PUT", body, signal });
  },
  delete<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return apiRequestAt<T>(LEGACY_API_BASE, path, { method: "DELETE", body, signal });
  },
  upload<T>(path: string, file: File, signal?: AbortSignal): Promise<T> {
    return rawApiRequest<T>(LEGACY_API_BASE, path, file, {
      "Content-Type": file.type || "application/octet-stream",
      "X-Personification-Video-Filename": file.name,
    }, signal);
  },
};

export { API_BASE, LEGACY_API_BASE, buildUrl, buildUrlAt };
