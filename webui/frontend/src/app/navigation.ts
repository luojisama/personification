import type { IconName } from "../components/Icon";

export type NavigationGroupId = "runtime" | "persona" | "capability" | "operations";

export interface NavigationNode {
  id: string;
  level: number;
  parent_id: string | null;
  path: string | null;
  label: string;
  aliases: string[];
  icon: IconName;
  default_child_id: string | null;
  legacy_view_id: string | null;
  data_source: string | null;
  children: NavigationNode[];
}

type LeafInput = { id: string; label: string; slug: string; aliases?: string[] };
type PageInput = { id: string; label: string; aliases: string[]; icon: IconName; legacy?: string; dataSource: string; leaves: LeafInput[] };

function page(groupId: NavigationGroupId, value: PageInput): NavigationNode {
  const parentPath = `/${groupId}/${value.id}`;
  const children = value.leaves.map<NavigationNode>((leaf) => ({
    id: `${groupId}.${value.id}.${leaf.id}`,
    level: 3,
    parent_id: `${groupId}.${value.id}`,
    path: `${parentPath}/${leaf.slug}`,
    label: leaf.label,
    aliases: leaf.aliases ?? [],
    icon: value.icon,
    default_child_id: null,
    legacy_view_id: null,
    data_source: value.dataSource,
    children: [],
  }));
  return {
    id: `${groupId}.${value.id}`,
    level: 2,
    parent_id: groupId,
    path: children[0]?.path ?? parentPath,
    label: value.label,
    aliases: value.aliases,
    icon: value.icon,
    default_child_id: children[0]?.id ?? null,
    legacy_view_id: value.legacy ?? null,
    data_source: value.dataSource,
    children,
  };
}

function group(id: NavigationGroupId, label: string, icon: IconName, pages: PageInput[]): NavigationNode {
  const children = pages.map((item) => page(id, item));
  return {
    id,
    level: 1,
    parent_id: null,
    path: children[0]?.path ?? null,
    label,
    aliases: [],
    icon,
    default_child_id: children[0]?.id ?? null,
    legacy_view_id: null,
    data_source: null,
    children,
  };
}

export const NAVIGATION_TREE: NavigationNode[] = [
  group("runtime", "运行", "signal", [
    { id: "overview", label: "总览", aliases: ["首页", "告警"], icon: "overview", dataSource: "/api/v2/overview", leaves: [{ id: "summary", label: "运行摘要", slug: "summary" }] },
    { id: "agent", label: "Agent 状态", aliases: ["运行时", "回合", "内存"], icon: "signal", legacy: "agent_status", dataSource: "/api/v2/runtime/agent", leaves: [{ id: "status", label: "实时状态", slug: "status" }, { id: "traces", label: "最近回合", slug: "traces" }] },
    { id: "tokens", label: "Token 统计", aliases: ["令牌", "模型消耗"], icon: "data", legacy: "dashboard", dataSource: "/api/v2/metrics/summary", leaves: [{ id: "24h", label: "24 小时", slug: "24h" }, { id: "7d", label: "7 天", slug: "7d" }, { id: "30d", label: "30 天", slug: "30d" }, { id: "all", label: "累计", slug: "all" }] },
    { id: "health", label: "功能体检", aliases: ["健康检查", "依赖"], icon: "shield", legacy: "health", dataSource: "/api/v2/health", leaves: [{ id: "catalog", label: "体检目录", slug: "catalog" }, { id: "operations", label: "运行记录", slug: "operations" }] },
    { id: "model-tests", label: "模型测试", aliases: ["聊天", "图片", "音频", "视频"], icon: "tool", legacy: "test", dataSource: "/api/v2/test-runs", leaves: [{ id: "chat", label: "聊天与人设", slug: "chat" }, { id: "media", label: "图片与音频", slug: "media" }, { id: "video-route", label: "视频路由探针", slug: "video-route" }, { id: "video-turn", label: "完整视频回合", slug: "video-turn" }] },
    { id: "routes", label: "路由能力", aliases: ["模型能力", "三态证据"], icon: "route", dataSource: "/api/v2/routes/capabilities", leaves: [{ id: "capabilities", label: "能力列表", slug: "capabilities" }, { id: "probes", label: "探针历史", slug: "probes" }, { id: "video", label: "视频证据", slug: "video" }] },
    { id: "proactive", label: "主动诊断", aliases: ["主动消息", "调度"], icon: "clock", legacy: "proactive", dataSource: "/api/v2/proactive", leaves: [{ id: "overview", label: "概览", slug: "overview" }, { id: "recent", label: "最近记录", slug: "recent" }, { id: "next", label: "下一可用窗口", slug: "next-eligible" }] },
    { id: "traces", label: "消息 Trace", aliases: ["追踪", "时间线"], icon: "trace", legacy: "traces", dataSource: "/api/v2/traces", leaves: [{ id: "index", label: "追踪索引", slug: "index" }, { id: "timeline", label: "时间线", slug: "timeline" }] },
    { id: "recovery", label: "恢复队列", aliases: ["失败恢复", "未知发送"], icon: "recovery", dataSource: "/api/v2/recovery", leaves: [{ id: "pending", label: "待恢复", slug: "pending" }, { id: "processing", label: "处理中", slug: "processing" }, { id: "unknown", label: "未知结果", slug: "unknown" }, { id: "history", label: "历史与过期", slug: "history" }] },
    { id: "qzone", label: "QQ 空间", aliases: ["动态", "评论", "点赞"], icon: "signal", legacy: "qzone", dataSource: "/api/v2/qzone", leaves: [{ id: "capabilities", label: "能力矩阵", slug: "capabilities" }, { id: "auth", label: "登录与恢复", slug: "auth" }, { id: "feeds", label: "只读动态", slug: "feeds" }, { id: "operations", label: "写操作", slug: "operations" }, { id: "history", label: "操作历史", slug: "history" }] },
  ]),
  group("persona", "拟人与记忆", "data", [
    { id: "personas", label: "用户画像", aliases: ["用户", "头像", "好感度"], icon: "data", legacy: "personas", dataSource: "/api/v2/personas", leaves: [{ id: "list", label: "画像列表", slug: "list" }, { id: "detail", label: "画像详情", slug: "detail" }, { id: "refresh", label: "后台刷新", slug: "refresh" }] },
    { id: "groups", label: "群信息", aliases: ["群目录", "成员", "群知识", "Peer Bot", "空间互动"], icon: "data", legacy: "groups", dataSource: "/api/v2/groups", leaves: [{ id: "list", label: "群列表", slug: "list" }, { id: "detail", label: "群详情", slug: "detail" }, { id: "knowledge", label: "知识与风格", slug: "knowledge" }, { id: "members", label: "成员与别名", slug: "members" }, { id: "peer-bots", label: "Peer Bot 协作", slug: "peer-bots" }, { id: "qzone-agent", label: "空间互动", slug: "qzone-agent" }] },
    { id: "group-switches", label: "群开关", aliases: ["白名单", "启用群"], icon: "settings", legacy: "group_switch", dataSource: "/api/v2/group-switches", leaves: [{ id: "list", label: "开关列表", slug: "list" }] },
    { id: "memories", label: "Agent 记忆", aliases: ["记忆", "召回", "向量"], icon: "data", legacy: "memory", dataSource: "/api/v2/memories", leaves: [{ id: "recent", label: "最近记忆", slug: "recent" }, { id: "search", label: "搜索与召回", slug: "search" }, { id: "index", label: "向量索引", slug: "vector-index" }] },
    { id: "memory-palace", label: "记忆宫殿", aliases: ["记忆图谱", "关系", "冲突"], icon: "route", legacy: "memory_graph", dataSource: "/api/v2/memory-palace", leaves: [{ id: "graph", label: "图谱", slug: "graph" }, { id: "zones", label: "分区", slug: "zones" }, { id: "conflicts", label: "关系与冲突", slug: "conflicts" }] },
    { id: "stickers", label: "表情包", aliases: ["贴纸", "标签", "索引"], icon: "data", legacy: "stickers", dataSource: "/api/v2/stickers", leaves: [{ id: "catalog", label: "贴纸目录", slug: "catalog" }, { id: "upload", label: "上传与编辑", slug: "upload" }, { id: "index", label: "索引任务", slug: "index" }] },
    { id: "persona-preview", label: "人设预览", aliases: ["Prompt", "安全上下文"], icon: "trace", legacy: "persona_prompt", dataSource: "/api/v2/persona-preview", leaves: [{ id: "prompt", label: "实际 Prompt", slug: "prompt" }, { id: "warnings", label: "质量告警", slug: "warnings" }] },
    { id: "persona-builder", label: "人设构建", aliases: ["模板", "候选", "历史"], icon: "tool", legacy: "persona_builder", dataSource: "/api/v2/persona-builder", leaves: [{ id: "tasks", label: "构建任务", slug: "tasks" }, { id: "candidate", label: "当前候选", slug: "candidate" }, { id: "history", label: "历史", slug: "history" }, { id: "templates", label: "模板", slug: "templates" }] },
  ]),
  group("capability", "能力", "tool", [
    { id: "skills", label: "Skill 管理", aliases: ["技能", "远程源"], icon: "tool", legacy: "skills", dataSource: "/api/v2/skills", leaves: [{ id: "installed", label: "已安装", slug: "installed" }, { id: "remote", label: "远程源", slug: "remote" }, { id: "health", label: "健康与审核", slug: "health" }] },
    { id: "mcp", label: "MCP 管理", aliases: ["工具服务", "授权", "社交研究"], icon: "tool", legacy: "mcp", dataSource: "/api/v2/mcp", leaves: [{ id: "registry", label: "Registry", slug: "registry" }, { id: "installations", label: "安装实例", slug: "installations" }, { id: "social", label: "社交研究", slug: "social" }, { id: "review", label: "授权与审核", slug: "review" }] },
    { id: "tool-creator", label: "创建工具", aliases: ["工具生成", "副作用"], icon: "tool", legacy: "tool_creator", dataSource: "/api/v2/tool-tasks", leaves: [{ id: "tasks", label: "任务列表", slug: "tasks" }, { id: "detail", label: "问题与事件", slug: "detail" }, { id: "artifacts", label: "产物与验证", slug: "artifacts" }] },
    { id: "plugin-knowledge", label: "插件知识库", aliases: ["插件命令", "覆盖率"], icon: "data", legacy: "plugin_knowledge", dataSource: "/api/v2/plugin-knowledge", leaves: [{ id: "catalog", label: "知识目录", slug: "catalog" }, { id: "search", label: "搜索", slug: "search" }, { id: "rebuild", label: "重建任务", slug: "rebuild" }] },
    { id: "plugins", label: "插件管理", aliases: ["版本", "更新", "测速"], icon: "system", legacy: "plugin_manager", dataSource: "/api/v2/plugin-update", leaves: [{ id: "status", label: "插件状态", slug: "status" }, { id: "update", label: "更新与测速", slug: "update" }, { id: "history", label: "提交与操作历史", slug: "history" }] },
  ]),
  group("operations", "运维", "settings", [
    { id: "config", label: "配置中心", aliases: ["模型配置", "媒体路由", "快速筛选"], icon: "settings", legacy: "config", dataSource: "/api/v2/config", leaves: [{ id: "general", label: "分类与配置", slug: "general" }, { id: "models", label: "模型与供应商", slug: "models" }, { id: "media", label: "媒体路由", slug: "media" }, { id: "integrations", label: "CLI 与集成", slug: "integrations" }] },
    { id: "user-policies", label: "用户策略与黑名单", aliases: ["黑名单", "门控"], icon: "shield", legacy: "user_policy", dataSource: "/api/v2/user-policies", leaves: [{ id: "list", label: "策略列表", slug: "list" }, { id: "detail", label: "详情与证据", slug: "detail" }, { id: "edit", label: "新增与修改", slug: "edit" }] },
    { id: "outbound", label: "近期 Bot 消息", aliases: ["发件箱", "撤回", "发送证据"], icon: "signal", legacy: "outbound", dataSource: "/api/v2/outbound", leaves: [{ id: "list", label: "消息记录", slug: "list" }, { id: "detail", label: "详情与证据", slug: "detail" }] },
    { id: "data-transfer", label: "数据迁移", aliases: ["导入", "导出", "回滚"], icon: "recovery", legacy: "data_transfer", dataSource: "/api/v2/backups", leaves: [{ id: "export", label: "导出", slug: "export" }, { id: "inspect", label: "Inspect 与 Dry-run", slug: "inspect" }, { id: "apply", label: "应用", slug: "apply" }, { id: "journal", label: "Journal 与回滚", slug: "journal" }] },
    { id: "audit", label: "审计日志", aliases: ["管理员操作", "操作详情"], icon: "trace", legacy: "audit", dataSource: "/api/v2/audit", leaves: [{ id: "overview", label: "概览", slug: "overview" }, { id: "records", label: "记录", slug: "records" }, { id: "detail", label: "详情", slug: "detail" }] },
    { id: "logs", label: "插件日志", aliases: ["实时日志", "Trace 过滤"], icon: "trace", legacy: "logs", dataSource: "/api/v2/logs", leaves: [{ id: "live", label: "实时流", slug: "live" }, { id: "history", label: "历史搜索", slug: "history" }, { id: "cleanup", label: "清理", slug: "cleanup" }] },
    { id: "qq", label: "QQ 管理", aliases: ["账号", "群", "好友"], icon: "signal", legacy: "qq", dataSource: "/api/v2/qq", leaves: [{ id: "accounts", label: "Bot 账号", slug: "accounts" }, { id: "groups", label: "群", slug: "groups" }, { id: "friends", label: "好友", slug: "friends" }, { id: "profile", label: "资料操作", slug: "profile" }] },
    { id: "devices", label: "设备管理", aliases: ["授权", "审批", "信任设备"], icon: "shield", legacy: "devices", dataSource: "/api/v2/devices", leaves: [{ id: "current", label: "当前设备", slug: "current" }, { id: "authorized", label: "已授权", slug: "authorized" }, { id: "pending", label: "待审批", slug: "pending" }, { id: "trusted", label: "信任设备", slug: "trusted" }] },
    { id: "systems", label: "系统诊断", aliases: ["多模态", "ffmpeg", "SSE"], icon: "system", dataSource: "/api/v2/multimodal/routes", leaves: [{ id: "multimodal", label: "多模态与依赖", slug: "multimodal" }, { id: "indexes", label: "索引与后台任务", slug: "indexes" }, { id: "realtime", label: "SSE 与运行环境", slug: "realtime" }] },
    { id: "settings", label: "设置", aliases: ["主题", "本地偏好", "版本"], icon: "settings", dataSource: "/api/v2/settings", leaves: [{ id: "preferences", label: "主题与偏好", slug: "preferences" }, { id: "security", label: "安全边界", slug: "security" }, { id: "version", label: "版本与迁移状态", slug: "version" }] },
  ]),
];

export const NAVIGATION_GROUPS = NAVIGATION_TREE;
export const NAVIGATION_ITEMS = NAVIGATION_TREE.flatMap((item) => item.children);
export const NAVIGATION_LEAVES = NAVIGATION_ITEMS.flatMap((item) => item.children);
export const LEGACY_VIEW_MAPPINGS = NAVIGATION_ITEMS.filter((item) => item.legacy_view_id);

export function navigationContext(pathname: string): { group: NavigationNode; page: NavigationNode; leaf: NavigationNode } | null {
  for (const groupNode of NAVIGATION_TREE) {
    for (const pageNode of groupNode.children) {
      const leaf = pageNode.children.find((item) => pathname === item.path || pathname.startsWith(`${item.path}/`));
      if (leaf) return { group: groupNode, page: pageNode, leaf };
    }
  }
  return null;
}

export const FLAT_ROUTE_REDIRECTS: Record<string, string> = {
  "/agent-status": "/runtime/agent/status", "/tokens": "/runtime/tokens/24h", "/health": "/runtime/health/catalog", "/model-tests": "/runtime/model-tests/chat",
  "/routes": "/runtime/routes/capabilities", "/proactive": "/runtime/proactive/recent", "/traces": "/runtime/traces/index", "/recovery": "/runtime/recovery/pending",
  "/qzone": "/runtime/qzone/capabilities", "/personas": "/persona/personas/list", "/groups": "/persona/groups/list", "/group-switches": "/persona/group-switches/list",
  "/memories": "/persona/memories/recent", "/memory-palace": "/persona/memory-palace/graph", "/stickers": "/persona/stickers/catalog",
  "/persona-preview": "/persona/persona-preview/prompt", "/persona-builder": "/persona/persona-builder/tasks", "/skills": "/capability/skills/installed",
  "/mcp": "/capability/mcp/registry", "/tool-creator": "/capability/tool-creator/tasks", "/plugin-knowledge": "/capability/plugin-knowledge/catalog",
  "/plugins": "/capability/plugins/status", "/config": "/operations/config/general", "/user-policies": "/operations/user-policies/list",
  "/outbound": "/operations/outbound/list", "/data-transfer": "/operations/data-transfer/export", "/audit": "/operations/audit/records",
  "/logs": "/operations/logs/live", "/qq": "/operations/qq/accounts", "/devices": "/operations/devices/current",
  "/systems": "/operations/systems/multimodal", "/settings": "/operations/settings/preferences",
};
