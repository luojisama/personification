import type { ReactNode } from "react";

import { EmptyState } from "./Panel";
import { StateBadge } from "./StateBadge";

export type BusinessRecord = Record<string, unknown>;

export interface BusinessColumn {
  key: string;
  label: string;
  render?: (row: BusinessRecord) => ReactNode;
}

export function asRecord(value: unknown): BusinessRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? value as BusinessRecord : {};
}

export function recordsAt(value: unknown, ...keys: string[]): BusinessRecord[] {
  const source = asRecord(value);
  for (const key of keys) {
    const rows = source[key];
    if (Array.isArray(rows)) return rows.map(asRecord);
  }
  return [];
}

export function textAt(row: BusinessRecord, ...keys: string[]): string {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
    if (typeof value === "boolean") return value ? "是" : "否";
  }
  return "—";
}

export function booleanAt(row: BusinessRecord, ...keys: string[]): boolean | null {
  for (const key of keys) {
    if (typeof row[key] === "boolean") return row[key] as boolean;
  }
  return null;
}

export function SafeStatus({ row, keys = ["status", "state", "outcome", "phase"] }: { row: BusinessRecord; keys?: string[] }) {
  const value = textAt(row, ...keys);
  const normalized = value.toLocaleLowerCase("zh-CN");
  const tone = /成功|完成|ok|sent|enabled|approved|succeeded/.test(normalized)
    ? "ok"
    : /失败|error|failed|denied|disabled|revoked/.test(normalized)
      ? "error"
      : /unknown|未知|partial|部分/.test(normalized)
        ? "unknown"
        : "info";
  return <StateBadge tone={tone} raw={value}>{value}</StateBadge>;
}

export function BusinessTable({
  rows,
  columns,
  rowKey,
  emptyCode,
  emptyText,
}: {
  rows: BusinessRecord[];
  columns: BusinessColumn[];
  rowKey: (row: BusinessRecord, index: number) => string;
  emptyCode: string;
  emptyText: string;
}) {
  if (!rows.length) return <EmptyState code={emptyCode}>{emptyText}</EmptyState>;
  return (
    <div className="trace-table-wrap">
      <table className="forensic-table">
        <thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={rowKey(row, index)}>
              {columns.map((column) => <td key={column.key} className="wrap-cell">{column.render ? column.render(row) : textAt(row, column.key)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
