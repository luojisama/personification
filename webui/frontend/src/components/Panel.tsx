import type { HTMLAttributes, ReactNode } from "react";

interface PanelProps extends HTMLAttributes<HTMLElement> {
  eyebrow?: string;
  title?: string;
  action?: ReactNode;
  as?: "section" | "article" | "aside";
}

export function Panel({ eyebrow, title, action, as: Tag = "section", className = "", children, ...props }: PanelProps) {
  return (
    <Tag className={`forensic-panel ${className}`.trim()} {...props}>
      {(eyebrow || title || action) && (
        <header className="panel-heading">
          <div>
            {eyebrow && <span className="panel-eyebrow">{eyebrow}</span>}
            {title && <h2>{title}</h2>}
          </div>
          {action && <div className="panel-action">{action}</div>}
        </header>
      )}
      <div className="panel-body">{children}</div>
    </Tag>
  );
}

export function PageHeader({ index, title, description, actions }: { index: string; title: string; description: string; actions?: ReactNode }) {
  return (
    <header className="page-heading">
      <div className="page-index" aria-hidden="true">{index}</div>
      <div className="page-title-block">
        <span className="page-kicker">PERSONIFICATION / ADMIN EVIDENCE DESK</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function EmptyState({ code, children }: { code: string; children: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-mark" aria-hidden="true">∅</span>
      <p>{children}</p>
      <code>{code}</code>
    </div>
  );
}
