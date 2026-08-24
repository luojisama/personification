import { describe, expect, it } from "vitest";

import { LEGACY_VIEW_MAPPINGS, NAVIGATION_GROUPS, NAVIGATION_ITEMS } from "./navigation";

const OLD_VIEWS = [
  "agent_status", "data_transfer", "dashboard", "config", "personas", "groups", "group_switch", "memory",
  "memory_graph", "stickers", "skills", "mcp", "tool_creator", "plugin_knowledge", "plugin_manager", "test",
  "persona_prompt", "persona_builder", "audit", "logs", "traces", "proactive", "health", "qzone", "qq",
  "devices", "user_policy", "outbound",
];

describe("新版功能对齐清单", () => {
  it("将旧版 28 个视图一一映射到独立 React 路由", () => {
    expect(LEGACY_VIEW_MAPPINGS).toHaveLength(28);
    expect(LEGACY_VIEW_MAPPINGS.map((item) => item.oldViewId).sort()).toEqual([...OLD_VIEWS].sort());
    expect(new Set(LEGACY_VIEW_MAPPINGS.map((item) => item.path)).size).toBe(28);
  });

  it("每个页面都声明服务端数据源、搜索别名和非空标签", () => {
    for (const item of NAVIGATION_ITEMS) {
      expect(item.label.trim()).not.toBe("");
      expect(item.aliases.length).toBeGreaterThan(0);
      expect(item.dataSource.startsWith("/api/")).toBe(true);
      expect(item.path).not.toContain("#");
    }
  });

  it("使用运行、拟人与记忆、能力、运维四组二级导航", () => {
    expect(NAVIGATION_GROUPS.map((group) => group.label)).toEqual(["运行", "拟人与记忆", "能力", "运维"]);
  });
});
