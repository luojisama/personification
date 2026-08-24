export function formatDateTime(value: string | number | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可解析";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDuration(milliseconds: number | null | undefined): string {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) return "—";
  if (milliseconds < 1_000) return `${Math.max(0, Math.round(milliseconds))} ms`;
  return `${(milliseconds / 1_000).toFixed(milliseconds >= 10_000 ? 1 : 2)} s`;
}

export function formatInteger(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("zh-CN").format(value)
    : "—";
}

export function shortId(value: string, edge = 8): string {
  if (!value) return "—";
  if (value.length <= edge * 2 + 1) return value;
  return `${value.slice(0, edge)}…${value.slice(-edge)}`;
}
