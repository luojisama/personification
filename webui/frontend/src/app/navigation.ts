import type { IconName } from "../components/Icon";

export type NavigationGroupId = "runtime" | "persona" | "capability" | "operations";

export interface NavigationItem {
  path: string;
  label: string;
  aliases: string[];
  icon: IconName;
  oldViewId?: string;
  dataSource: string;
}

export interface NavigationGroup {
  id: NavigationGroupId;
  label: string;
  items: NavigationItem[];
}

export const NAVIGATION_GROUPS: NavigationGroup[] = [
  {
    id: "runtime",
    label: "运行",
    items: [
      { path: "/", label: "总览", aliases: ["首页", "状态"], icon: "overview", dataSource: "/api/v2/overview" },
      { path: "/agent-status", label: "Agent 状态", aliases: ["运行时", "内存", "回合"], icon: "signal", oldViewId: "agent_status", dataSource: "/api/v2/runtime/agent" },
      { path: "/tokens", label: "Token 统计", aliases: ["令牌", "模型消耗", "仪表盘"], icon: "data", oldViewId: "dashboard", dataSource: "/api/v2/metrics/summary" },
      { path: "/health", label: "功能体检", aliases: ["健康检查", "依赖检查"], icon: "shield", oldViewId: "health", dataSource: "/api/v2/health" },
      { path: "/model-tests", label: "模型测试", aliases: ["聊天测试", "人设 Prompt", "视频测试"], icon: "tool", oldViewId: "test", dataSource: "/api/test/*" },
      { path: "/routes", label: "路由能力", aliases: ["视觉", "音频", "视频", "函数"], icon: "route", dataSource: "/api/v2/routes/capabilities" },
      { path: "/proactive", label: "主动诊断", aliases: ["主动消息", "调度"], icon: "clock", oldViewId: "proactive", dataSource: "/api/proactive/*" },
      { path: "/traces", label: "消息 Trace", aliases: ["追踪", "时间线", "诊断"], icon: "trace", oldViewId: "traces", dataSource: "/api/v2/traces" },
      { path: "/recovery", label: "恢复队列", aliases: ["失败恢复", "补发"], icon: "recovery", dataSource: "/api/v2/recovery" },
      { path: "/qzone", label: "QQ 空间", aliases: ["动态", "评论", "点赞"], icon: "signal", oldViewId: "qzone", dataSource: "/api/v2/qzone/capabilities" },
    ],
  },
  {
    id: "persona",
    label: "拟人与记忆",
    items: [
      { path: "/personas", label: "用户画像", aliases: ["用户", "头像", "好感度"], icon: "data", oldViewId: "personas", dataSource: "/api/v2/personas" },
      { path: "/groups", label: "群信息", aliases: ["群目录", "群头像", "成员"], icon: "data", oldViewId: "groups", dataSource: "/api/v2/groups" },
      { path: "/group-switches", label: "群开关", aliases: ["白名单", "启用群"], icon: "settings", oldViewId: "group_switch", dataSource: "/api/groups/whitelist" },
      { path: "/memories", label: "Agent 记忆", aliases: ["记忆", "来源", "过期"], icon: "data", oldViewId: "memory", dataSource: "/api/v2/memories" },
      { path: "/memory-palace", label: "记忆宫殿", aliases: ["记忆图谱", "关系"], icon: "route", oldViewId: "memory_graph", dataSource: "/api/memory/graph" },
      { path: "/stickers", label: "表情包", aliases: ["贴纸", "索引", "标签"], icon: "data", oldViewId: "stickers", dataSource: "/api/v2/stickers" },
      { path: "/persona-preview", label: "人设预览", aliases: ["Prompt", "提示词"], icon: "trace", oldViewId: "persona_prompt", dataSource: "/api/test/persona-prompt" },
      { path: "/persona-builder", label: "人设构建", aliases: ["模板", "角色"], icon: "tool", oldViewId: "persona_builder", dataSource: "/api/persona-template/*" },
    ],
  },
  {
    id: "capability",
    label: "能力",
    items: [
      { path: "/skills", label: "Skill 管理", aliases: ["技能"], icon: "tool", oldViewId: "skills", dataSource: "/api/v2/skills" },
      { path: "/mcp", label: "MCP 管理", aliases: ["工具服务", "授权"], icon: "tool", oldViewId: "mcp", dataSource: "/api/v2/mcp" },
      { path: "/tool-creator", label: "创建工具", aliases: ["工具生成", "副作用"], icon: "tool", oldViewId: "tool_creator", dataSource: "/api/v2/tool-tasks" },
      { path: "/plugin-knowledge", label: "插件知识库", aliases: ["插件命令", "知识索引"], icon: "data", oldViewId: "plugin_knowledge", dataSource: "/api/v2/plugin-knowledge" },
      { path: "/plugins", label: "插件管理", aliases: ["版本", "更新", "启停"], icon: "system", oldViewId: "plugin_manager", dataSource: "/api/plugin-manager/status" },
    ],
  },
  {
    id: "operations",
    label: "运维",
    items: [
      { path: "/config", label: "配置中心", aliases: ["设置", "模型配置", "快速筛选"], icon: "settings", oldViewId: "config", dataSource: "/api/v2/config" },
      { path: "/user-policies", label: "用户策略与黑名单", aliases: ["Blacklist", "禁用用户"], icon: "shield", oldViewId: "user_policy", dataSource: "/api/user-policy/states" },
      { path: "/outbound", label: "近期 Bot 消息", aliases: ["发件箱", "发送结果", "撤回"], icon: "signal", oldViewId: "outbound", dataSource: "/api/outbound/recent" },
      { path: "/data-transfer", label: "数据迁移", aliases: ["导入", "导出", "备份", "回滚"], icon: "recovery", oldViewId: "data_transfer", dataSource: "/api/v2/backups/*" },
      { path: "/audit", label: "审计日志", aliases: ["管理员操作"], icon: "trace", oldViewId: "audit", dataSource: "/api/audit/recent" },
      { path: "/logs", label: "插件日志", aliases: ["运行日志", "错误"], icon: "trace", oldViewId: "logs", dataSource: "/api/v2/logs" },
      { path: "/qq", label: "QQ 管理", aliases: ["好友", "群管理", "账号"], icon: "signal", oldViewId: "qq", dataSource: "/api/qq/*" },
      { path: "/devices", label: "设备管理", aliases: ["登录设备", "信任"], icon: "shield", oldViewId: "devices", dataSource: "/api/auth/devices" },
      { path: "/systems", label: "系统诊断", aliases: ["多模态", "依赖", "ffmpeg"], icon: "system", dataSource: "/api/v2/multimodal/routes" },
      { path: "/settings", label: "设置", aliases: ["主题", "安全边界"], icon: "settings", dataSource: "/api/v2/settings" },
    ],
  },
];

export const NAVIGATION_ITEMS = NAVIGATION_GROUPS.flatMap((group) => group.items);
export const LEGACY_VIEW_MAPPINGS = NAVIGATION_ITEMS.filter((item) => item.oldViewId);
