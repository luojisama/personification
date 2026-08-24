import { Link } from "react-router-dom";

import { EmptyState, PageHeader } from "../components/Panel";

export function NotFoundPage() {
  return (
    <div className="page-stack">
      <PageHeader index="404" title="未找到此卷宗" description="这个管理台路径不存在，或对应页面尚未迁移。" />
      <EmptyState code="frontend_route_not_found">
        返回 <Link to="/">事件总览</Link>，继续核对当前运行状态。
      </EmptyState>
    </div>
  );
}
