import { Icon } from "./Icon";

type BadgeTone = "ok" | "warn" | "error" | "unknown" | "info" | "running";

export function StateBadge({ tone, children, raw }: { tone: BadgeTone; children: React.ReactNode; raw?: string }) {
  return (
    <span className={`state-badge state-${tone}`} title={raw ? `原始状态：${raw}` : undefined}>
      <span className="state-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

export function CapabilityMark({ state, label, title }: { state: "supported" | "unsupported" | "unknown"; label: string; title: string }) {
  const icon = state === "supported" ? "check" : state === "unsupported" ? "close" : "unknown";
  return (
    <span className={`capability-mark capability-${state}`} title={title} aria-label={`${label}：${state === "supported" ? "支持" : state === "unsupported" ? "不支持" : "未知"}`}>
      <Icon name={icon} />
      <span>{label}</span>
    </span>
  );
}
