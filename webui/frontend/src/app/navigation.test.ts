import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { FEATURE_PARITY_CONTRACTS } from "./parityContracts";
import { FLAT_ROUTE_REDIRECTS, LEGACY_VIEW_MAPPINGS, NAVIGATION_GROUPS, NAVIGATION_ITEMS, NAVIGATION_LEAVES, navigationContext } from "./navigation";

const OLD_VIEWS = [
  "agent_status", "data_transfer", "dashboard", "config", "personas", "groups", "group_switch", "memory",
  "memory_graph", "stickers", "skills", "mcp", "tool_creator", "plugin_knowledge", "plugin_manager", "test",
  "persona_prompt", "persona_builder", "audit", "logs", "traces", "proactive", "health", "qzone", "qq",
  "devices", "user_policy", "outbound",
];

describe("新版分层导航与行为对齐合同", () => {
  it("左侧只保留运行、拟人与记忆、能力、运维四个一级分类", () => {
    expect(NAVIGATION_GROUPS.map((group) => group.label)).toEqual(["运行", "拟人与记忆", "能力", "运维"]);
    expect(NAVIGATION_GROUPS.every((group) => group.level === 1 && group.parent_id === null)).toBe(true);
  });

  it("二级页面和三级功能形成可深链的递归导航树", () => {
    expect(NAVIGATION_ITEMS.every((item) => item.level === 2 && item.default_child_id)).toBe(true);
    expect(NAVIGATION_LEAVES.every((item) => item.level === 3 && item.path?.split("/").length === 4)).toBe(true);
    expect(navigationContext("/runtime/proactive/recent")?.leaf.label).toBe("最近记录");
    expect(navigationContext("/operations/config/media")?.page.label).toBe("配置中心");
  });

  it("旧版 28 个视图都有独立业务组件和行为合同", () => {
    expect(LEGACY_VIEW_MAPPINGS).toHaveLength(28);
    expect(LEGACY_VIEW_MAPPINGS.map((item) => item.legacy_view_id).sort()).toEqual([...OLD_VIEWS].sort());
    expect(FEATURE_PARITY_CONTRACTS).toHaveLength(28);
    expect(FEATURE_PARITY_CONTRACTS.map((item) => item.legacy_view_id).sort()).toEqual([...OLD_VIEWS].sort());
    for (const item of FEATURE_PARITY_CONTRACTS) {
      expect(item.component).not.toMatch(/FeatureWorkbench|RuntimeCatalog|ManagementData/);
      expect(item.read_capabilities.length).toBeGreaterThan(0);
      expect(item.acceptance_tests.length).toBeGreaterThan(0);
      expect(item.loading_state).not.toBe(item.empty_state);
      if (item.write_capabilities.length) {
        expect(item.write_risk).not.toBe("none");
        expect(item.confirmation_required).toBe(true);
      }
    }
  });

  it("保留旧扁平地址重定向且不保留 data/catalog 万能入口", () => {
    expect(FLAT_ROUTE_REDIRECTS["/proactive"]).toBe("/runtime/proactive/recent");
    expect(FLAT_ROUTE_REDIRECTS["/group-switches"]).toBe("/persona/group-switches/list");
    expect(FLAT_ROUTE_REDIRECTS).not.toHaveProperty("/data");
    expect(FLAT_ROUTE_REDIRECTS).not.toHaveProperty("/catalog");
  });

  it("生产 Router 不再引用通用 JSON 扁平化与旧接口资源", () => {
    const root = resolve(process.cwd(), "src");
    const routerSource = readFileSync(resolve(root, "app/router.tsx"), "utf8");
    const resourcesSource = readFileSync(resolve(root, "api/resources.ts"), "utf8");
    const configSource = readFileSync(resolve(root, "pages/ConfigCenterPage.tsx"), "utf8");
    expect(routerSource).not.toContain("FeatureWorkbenchPage");
    expect(routerSource).not.toContain("RuntimeCatalogPage");
    expect(routerSource).not.toContain("ManagementDataPage");
    expect(routerSource).toContain("errorElement: <RouteErrorPage />");
    expect(routerSource).toContain("frontend_chunk_load_failed");
    expect(resourcesSource).not.toContain("resources.legacy");
    expect(resourcesSource).not.toContain("configAll(");
    expect(configSource).not.toContain("JSON.stringify");
    expect(configSource).toContain("StructuredListInput");
    expect(configSource).toContain("StructuredObjectInput");
    for (const contract of FEATURE_PARITY_CONTRACTS) {
      expect(routerSource, `${contract.legacy_view_id} must use ${contract.component}`).toContain(contract.component);
    }
  });

  it("Trace 详情链接保持在分层路由内并兼容旧深链", () => {
    const root = resolve(process.cwd(), "src");
    const routerSource = readFileSync(resolve(root, "app/router.tsx"), "utf8");
    const tracePage = readFileSync(resolve(root, "pages/TracesPage.tsx"), "utf8");
    const overviewPage = readFileSync(resolve(root, "pages/OverviewPage.tsx"), "utf8");
    const agentPage = readFileSync(resolve(root, "pages/AgentStatusPage.tsx"), "utf8");
    for (const source of [tracePage, overviewPage, agentPage]) {
      expect(source).not.toContain("`/traces/${");
      expect(source).toContain("/runtime/traces/timeline/");
    }
    expect(routerSource).toContain('path: "traces/:traceId"');
    expect(routerSource).toContain("LegacyTraceRedirect");
  });
});
