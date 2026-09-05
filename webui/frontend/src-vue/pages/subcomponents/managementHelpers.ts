export type JsonRecord = Record<string, unknown>;

export function record(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}

export function records(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
}

export function text(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) {
    return value
      .slice(0, 16)
      .filter((item) => ["string", "number", "boolean"].includes(typeof item))
      .map((item) => String(item))
      .join("、") || fallback;
  }
  if (typeof value === "object") return fallback;
  return String(value);
}

export function favorabilityDisplayClass(score: unknown): string {
  return Number(score) < 0 ? "state-error" : "";
}
