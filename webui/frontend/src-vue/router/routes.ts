import type { RouteRecordRaw } from "vue-router";

import { FLAT_ROUTE_REDIRECTS } from "@/app/navigation";
import AgentStatusPage from "@vue-app/pages/AgentStatusPage.vue";
import CapabilityBusinessPages from "@vue-app/pages/CapabilityBusinessPages.vue";
import ConfigOperationsPages from "@vue-app/pages/ConfigOperationsPages.vue";
import FunctionalTestsPage from "@vue-app/pages/FunctionalTestsPage.vue";
import GroupSwitchesPage from "@vue-app/pages/GroupSwitchesPage.vue";
import ManagementDataPage from "@vue-app/pages/ManagementDataPage.vue";
import MemoryPages from "@vue-app/pages/MemoryPages.vue";
import ModelTestsPage from "@vue-app/pages/ModelTestsPage.vue";
import NotFoundPage from "@vue-app/pages/NotFoundPage.vue";
import OverviewPage from "@vue-app/pages/OverviewPage.vue";
import OperationsBusinessPages from "@vue-app/pages/OperationsBusinessPages.vue";
import PersonaFeaturePages from "@vue-app/pages/PersonaFeaturePages.vue";
import ProactiveDiagnosticsPage from "@vue-app/pages/ProactiveDiagnosticsPage.vue";
import QzoneCapabilitiesPage from "@vue-app/pages/QzoneCapabilitiesPage.vue";
import RecoveryPage from "@vue-app/pages/RecoveryPage.vue";
import RouteCapabilitiesPage from "@vue-app/pages/RouteCapabilitiesPage.vue";
import SystemDiagnosticsPage from "@vue-app/pages/SystemDiagnosticsPage.vue";
import TokenStatisticsPage from "@vue-app/pages/TokenStatisticsPage.vue";
import TracesPage from "@vue-app/pages/TracesPage.vue";

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
  { path: "/capability/skills/:section", name: "capability-skills", component: CapabilityBusinessPages, props: { mode: "skills" }, meta: { title: "Skill 管理" } },
  { path: "/capability/mcp/:section", name: "capability-mcp", component: CapabilityBusinessPages, props: { mode: "mcp" }, meta: { title: "MCP 管理" } },
  { path: "/capability/tool-creator/:section", name: "capability-tool-creator", component: CapabilityBusinessPages, props: { mode: "tool-creator" }, meta: { title: "创建工具" } },
  { path: "/capability/plugin-knowledge/:section", name: "capability-plugin-knowledge", component: CapabilityBusinessPages, props: { mode: "plugin-knowledge" }, meta: { title: "插件知识库" } },
  { path: "/capability/plugins/:section", name: "capability-plugins", component: CapabilityBusinessPages, props: { mode: "plugins" }, meta: { title: "插件管理" } },
  { path: "/operations/config/:section", name: "operations-config", component: ConfigOperationsPages, props: { mode: "config" }, meta: { title: "配置中心" } },
  { path: "/operations/user-policies/:section", name: "operations-user-policies", component: OperationsBusinessPages, props: { mode: "user-policies" }, meta: { title: "用户策略与黑名单" } },
  { path: "/operations/outbound/:section", name: "operations-outbound", component: OperationsBusinessPages, props: { mode: "outbound" }, meta: { title: "近期 Bot 消息" } },
  { path: "/operations/data-transfer/:section", name: "operations-data-transfer", component: OperationsBusinessPages, props: { mode: "data-transfer" }, meta: { title: "数据迁移" } },
  { path: "/operations/audit/:section", name: "operations-audit", component: OperationsBusinessPages, props: { mode: "audit" }, meta: { title: "审计日志" } },
  { path: "/operations/logs/:section", name: "operations-logs", component: ConfigOperationsPages, props: { mode: "logs" }, meta: { title: "插件日志" } },
  { path: "/operations/qq/:section", name: "operations-qq", component: OperationsBusinessPages, props: { mode: "qq" }, meta: { title: "QQ 管理" } },
  { path: "/operations/devices/:section", name: "operations-devices", component: OperationsBusinessPages, props: { mode: "devices" }, meta: { title: "设备管理" } },
  { path: "/operations/systems/:section", name: "operations-systems", component: SystemDiagnosticsPage, meta: { title: "系统诊断" } },
  { path: "/operations/settings/:section", name: "operations-settings", component: ConfigOperationsPages, props: { mode: "settings" }, meta: { title: "设置" } },
  ...flatRedirectRoutes,
  {
    path: "/:pathMatch(.*)*",
    name: "not-found",
    component: NotFoundPage,
    meta: { title: "页面未找到" },
  },
];
