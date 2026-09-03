import type { RouteRecordRaw } from "vue-router";

import { FLAT_ROUTE_REDIRECTS } from "@/app/navigation";
import NotFoundPage from "@vue-app/pages/NotFoundPage.vue";
import PlaceholderPage from "@vue-app/pages/PlaceholderPage.vue";

function previewRoute(path: string, name: string, title: string): RouteRecordRaw {
  return {
    path,
    name,
    component: PlaceholderPage,
    meta: { title, description: `${title}业务页面正在迁移至 Vue 管理台。` },
  };
}

const flatRedirectRoutes: RouteRecordRaw[] = Object.entries(FLAT_ROUTE_REDIRECTS).map(
  ([path, target]) => ({
    path,
    redirect: (route) => ({ path: target, query: route.query, hash: route.hash }),
  }),
);

export const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/runtime/overview/summary" },
  previewRoute("/runtime/overview/summary", "runtime-overview-summary", "运行摘要"),
  previewRoute("/runtime/agent/:section", "runtime-agent", "Agent 状态"),
  previewRoute("/runtime/tokens/:window", "runtime-tokens", "Token 统计"),
  previewRoute("/runtime/health/:section", "runtime-health", "功能体检"),
  previewRoute("/runtime/model-tests/:section", "runtime-model-tests", "模型测试"),
  previewRoute("/runtime/routes/:section", "runtime-routes", "路由能力"),
  previewRoute("/runtime/proactive/:section", "runtime-proactive", "主动诊断"),
  previewRoute("/runtime/traces/index", "runtime-traces-index", "追踪索引"),
  previewRoute("/runtime/traces/timeline", "runtime-traces-timeline", "Trace 时间线"),
  previewRoute("/runtime/traces/timeline/:traceId", "runtime-traces-detail", "Trace 详情"),
  {
    path: "/traces/:traceId",
    redirect: (route) => ({
      path: `/runtime/traces/timeline/${encodeURIComponent(String(route.params.traceId ?? ""))}`,
      query: route.query,
      hash: route.hash,
    }),
  },
  previewRoute("/runtime/recovery/:section", "runtime-recovery", "恢复队列"),
  previewRoute("/runtime/qzone/:section", "runtime-qzone", "QQ 空间"),
  previewRoute("/persona/personas/:section", "persona-personas", "用户画像"),
  previewRoute("/persona/groups/:section", "persona-groups", "群信息"),
  previewRoute("/persona/group-switches/list", "persona-group-switches", "群开关"),
  previewRoute("/persona/memories/:section", "persona-memories", "Agent 记忆"),
  previewRoute("/persona/memory-palace/:section", "persona-memory-palace", "记忆宫殿"),
  previewRoute("/persona/stickers/:section", "persona-stickers", "表情包"),
  previewRoute("/persona/persona-preview/:section", "persona-preview", "人设预览"),
  previewRoute("/persona/persona-builder/:section", "persona-builder", "人设构建"),
  previewRoute("/capability/skills/:section", "capability-skills", "Skill 管理"),
  previewRoute("/capability/mcp/:section", "capability-mcp", "MCP 管理"),
  previewRoute("/capability/tool-creator/:section", "capability-tool-creator", "创建工具"),
  previewRoute("/capability/plugin-knowledge/:section", "capability-plugin-knowledge", "插件知识库"),
  previewRoute("/capability/plugins/:section", "capability-plugins", "插件管理"),
  previewRoute("/operations/config/:section", "operations-config", "配置中心"),
  previewRoute("/operations/user-policies/:section", "operations-user-policies", "用户策略与黑名单"),
  previewRoute("/operations/outbound/:section", "operations-outbound", "近期 Bot 消息"),
  previewRoute("/operations/data-transfer/:section", "operations-data-transfer", "数据迁移"),
  previewRoute("/operations/audit/:section", "operations-audit", "审计日志"),
  previewRoute("/operations/logs/:section", "operations-logs", "插件日志"),
  previewRoute("/operations/qq/:section", "operations-qq", "QQ 管理"),
  previewRoute("/operations/devices/:section", "operations-devices", "设备管理"),
  previewRoute("/operations/systems/:section", "operations-systems", "系统诊断"),
  previewRoute("/operations/settings/:section", "operations-settings", "设置"),
  ...flatRedirectRoutes,
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: NotFoundPage,
    meta: { title: "页面未找到" },
  },
];
