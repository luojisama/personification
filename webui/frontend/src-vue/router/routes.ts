import type { RouteRecordRaw } from "vue-router";

import { FLAT_ROUTE_REDIRECTS } from "@/app/navigation";
import AgentStatusPage from "@vue-app/pages/AgentStatusPage.vue";
import FunctionalTestsPage from "@vue-app/pages/FunctionalTestsPage.vue";
import GroupSwitchesPage from "@vue-app/pages/GroupSwitchesPage.vue";
import ManagementDataPage from "@vue-app/pages/ManagementDataPage.vue";
import MemoryPages from "@vue-app/pages/MemoryPages.vue";
import ModelTestsPage from "@vue-app/pages/ModelTestsPage.vue";
import NotFoundPage from "@vue-app/pages/NotFoundPage.vue";
import OverviewPage from "@vue-app/pages/OverviewPage.vue";
import PersonaFeaturePages from "@vue-app/pages/PersonaFeaturePages.vue";
import PlaceholderPage from "@vue-app/pages/PlaceholderPage.vue";
import ProactiveDiagnosticsPage from "@vue-app/pages/ProactiveDiagnosticsPage.vue";
import QzoneCapabilitiesPage from "@vue-app/pages/QzoneCapabilitiesPage.vue";
import RecoveryPage from "@vue-app/pages/RecoveryPage.vue";
import RouteCapabilitiesPage from "@vue-app/pages/RouteCapabilitiesPage.vue";
import SystemDiagnosticsPage from "@vue-app/pages/SystemDiagnosticsPage.vue";
import TokenStatisticsPage from "@vue-app/pages/TokenStatisticsPage.vue";
import TracesPage from "@vue-app/pages/TracesPage.vue";

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
  { path: "/runtime/overview/summary", name: "runtime-overview-summary", component: OverviewPage, meta: { title: "运行摘要" } },
  { path: "/runtime/agent/:section", name: "runtime-agent", component: AgentStatusPage, meta: { title: "Agent 状态" } },
  { path: "/runtime/tokens/:window", name: "runtime-tokens", component: TokenStatisticsPage, meta: { title: "Token 统计" } },
  { path: "/runtime/health/:section", name: "runtime-health", component: FunctionalTestsPage, meta: { title: "功能体检" } },
  { path: "/runtime/model-tests/:section", name: "runtime-model-tests", component: ModelTestsPage, meta: { title: "模型测试" } },
  { path: "/runtime/routes/:section", name: "runtime-routes", component: RouteCapabilitiesPage, meta: { title: "路由能力" } },
  { path: "/runtime/proactive/:section", name: "runtime-proactive", component: ProactiveDiagnosticsPage, meta: { title: "主动诊断" } },
  { path: "/runtime/traces/index", name: "runtime-traces-index", component: TracesPage, meta: { title: "追踪索引" } },
  { path: "/runtime/traces/timeline", name: "runtime-traces-timeline", component: TracesPage, meta: { title: "Trace 时间线" } },
  { path: "/runtime/traces/timeline/:traceId", name: "runtime-traces-detail", component: TracesPage, meta: { title: "Trace 详情" } },
  {
    path: "/traces/:traceId",
    redirect: (route) => ({
      path: `/runtime/traces/timeline/${encodeURIComponent(String(route.params.traceId ?? ""))}`,
      query: route.query,
      hash: route.hash,
    }),
  },
  { path: "/runtime/recovery/:section", name: "runtime-recovery", component: RecoveryPage, meta: { title: "恢复队列" } },
  { path: "/runtime/qzone/:section", name: "runtime-qzone", component: QzoneCapabilitiesPage, meta: { title: "QQ 空间" } },
  { path: "/persona/personas/:section", name: "persona-personas", component: ManagementDataPage, props: { dataset: "personas" }, meta: { title: "用户画像" } },
  { path: "/persona/groups/:section", name: "persona-groups", component: ManagementDataPage, props: { dataset: "groups" }, meta: { title: "群信息" } },
  { path: "/persona/group-switches/list", name: "persona-group-switches", component: GroupSwitchesPage, meta: { title: "群开关" } },
  { path: "/persona/memories/:section", name: "persona-memories", component: MemoryPages, meta: { title: "Agent 记忆", mode: "memory" } },
  { path: "/persona/memory-palace/:section", name: "persona-memory-palace", component: MemoryPages, meta: { title: "记忆宫殿", mode: "palace" } },
  { path: "/persona/stickers/:section", name: "persona-stickers", component: PersonaFeaturePages, meta: { title: "表情包", mode: "stickers" } },
  { path: "/persona/persona-preview/:section", name: "persona-preview", component: PersonaFeaturePages, meta: { title: "人设预览", mode: "preview" } },
  { path: "/persona/persona-builder/:section", name: "persona-builder", component: PersonaFeaturePages, meta: { title: "人设构建", mode: "builder" } },
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
  { path: "/operations/systems/:section", name: "operations-systems", component: SystemDiagnosticsPage, meta: { title: "系统诊断" } },
  previewRoute("/operations/settings/:section", "operations-settings", "设置"),
  ...flatRedirectRoutes,
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: NotFoundPage,
    meta: { title: "页面未找到" },
  },
];
