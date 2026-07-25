# 原生社交平台查梗 MCP

`builtin_social_platform_research` 是项目内置、只读、默认关闭的 MCP 服务，用于在管理员自己的登录态下检索 B站、抖音、贴吧和小黑盒，补足普通网页搜索无法稳定覆盖的游戏黑话、梗、外号和版本语境。平台材料始终标记为 `untrusted_data_only`；MCP 只负责取得、标准化和过滤材料，词义提取、sense 聚类、多源确认和词典写入由宿主侧 Agent 流水线完成。

## 开启与登录

1. 启动 FastAPI Driver，使用管理员账号进入 WebUI。
2. 打开“MCP → 原生 MCP”，开启服务总开关。
3. 逐个平台开启 B站、抖音、贴吧或小黑盒。
4. 点击“扫码登录”；若平台要求设备确认、短信验证码、滑块或其它人工验证，使用 WebUI 打开的官方页面完成。
5. 平台状态变为 `ready` 后，再逐项授权 Agent 工具。
6. 使用检索预览读取一个真实词，确认封面、标题、正文/文案、评论/回复和能力矩阵符合预期。

停止或重载服务不会清除登录态。注销要求 WebUI 精确确认，只删除所选平台的独立 profile，不影响其它平台。服务不实现验证码或风控绕过，也不承诺平台页面改版后无需更新选择器。

## 三层可用条件

一次 Agent 调用必须同时满足：

- MCP 服务总开关开启且子进程存活；
- 请求指向的平台至少有一个已开启并处于 `ready`；
- 对应工具已经由管理员授权并注册。

平台被关闭后，搜索与读取不会再访问该平台。一个平台出现 `risk_controlled`、`manual_verification_required`、登录过期或浏览器异常时，其它健康平台仍可返回 `partial=true` 的内容包。

## Agent 工具

| 工具 | 用途 | 写操作 |
|---|---|---|
| `social_content_search` | 按查询词、平台、内容类型和质量模式搜索内容卡片 | 无 |
| `social_content_read` | 按平台内容 ID 或 URL 读取正文、评论、回复、弹幕/字幕 | 无 |
| `research_game_slang` | 针对一个词和当前游戏语境做跨平台两阶段查证 | 无 |

管理面的登录、状态、配置、取消和注销使用私有 JSON-RPC，只允许 WebUI 后端调用，不出现在 `tools/list`。MCP 不公开任意 HTTP、任意浏览器操作、任意 JavaScript、Cookie 导出或平台管理工具。

## 平台能力

| 平台 | 搜索 | 正文/文案 | 评论/回复 | 弹幕 | 备注 |
|---|---:|---:|---:|---:|---|
| B站 | 是 | 是 | 是 | 是 | 可取得时同时读取字幕 |
| 抖音 | 是 | 是 | 是 | 页面提供时 | 登录与风控状态以当前官方页面为准 |
| 贴吧 | 是 | 是 | 楼层与回复 | 否 | 支持全局/吧内页面结果 |
| 小黑盒 | 是 | 是 | 是 | 仅真实视频支持时 | 文章、动态和帖子按实际页面能力声明 |

每个平台使用独立 Playwright persistent context。适配器优先解析官方页面与页面自身产生的 XHR/Fetch 响应，不维护通用私有签名客户端。

## 来源质量与缓存

默认过滤规则如下：

- 视频：`marketing_score >= 0.75` 排除；仅在 `play_count < 3000` 且 `comment_count < 5` 同时成立时按低热度排除。
- 文章/帖子：明确营销内容排除；默认仅在 `reply_count < 3` 且其它已知互动均为零时按低互动排除。
- `balanced` 会执行默认过滤，`strict` 更保守，`ranking_only` 保留候选并只影响排序。
- 营销信号必须给出原因，例如商业标识、联系方式、外部导流、重复文案、外链或行动号召密度。
- 标准化搜索/内容包默认缓存 6 小时；不缓存完整视频、Cookie 或原始整页 HTML。

评论、回复、弹幕采样量、平台阈值、最大结果数、缓存时间和限流均可在平台卡片单独调整。封面仅通过短期 `cover_ref` 代理读取，代理限制平台域名、HTTPS、重定向、图片 Content-Type 和 5 MiB 大小。

## 多梗提取与自动学习

一份内容包默认最多提取 20 个 claim，可在 1–50 之间配置。同一视频可以同时解释“刘涛”“牢大”“大红”等多个 term；每条 claim 必须引用标题、正文、评论、回复或弹幕中的具体“词语 → 含义”片段。只有出现词语、没有解释关系的共现不算证据。

独立来源按内容簇计数：同一内容下再多评论也只贡献一份；跨平台搬运、相同媒体指纹、明确转载、相同外部来源或高度近似正文会合并。营销过滤、失效引用、风控不完整和低于 `claim_min_confidence` 的材料不参与自动确认。

状态机：

- `observed`：一个独立内容，仅留作候选；
- `understand_only`：默认至少两个独立内容一致，只可理解或被问时解释；
- `verified`：默认至少三个独立内容、覆盖至少两个平台，可在匹配语境中自然使用；
- `disputed`：同一游戏/版本下相反含义也达到实质支持，停止主动使用；
- `stale`：长期没有新鲜证据，停止主动使用但保留历史；
- `rejected`：管理员拒绝；
- `manual_locked`：管理员确认或编辑，自动流水线只能追加证据和提示冲突。

不同游戏、版本或赛季保存为不同 sense，不以简单多数覆盖。自动词义携带 `game_context`、`version_context`、`usage_context` 和 `safe_usage`；没有匹配上下文时不会注入游戏义。

## 配置

| 配置键 | 默认值 | WebUI 约束 | 说明 |
|---|---:|---:|---|
| `personification_meme_reply_probability` | `0.18` | `0–1` | 已决定回复后，是否允许自然带一个低风险 active sense |
| `personification_slang_max_claims` | `20` | `1–50` | 单个内容包最多提取的 claim 数 |
| `personification_auto_understand_min_sources` | `2` | `2–20` | 进入 `understand_only` 的独立内容数 |
| `personification_auto_use_min_sources` | `3` | `2–30` | 进入 `verified` 的独立内容数 |
| `personification_auto_use_min_platforms` | `2` | `2–4` | 进入 `verified` 的平台覆盖数 |
| `personification_claim_min_confidence` | `0.72` | `0–1` | claim 进入自动聚合的最低置信度 |
| `personification_semantic_equivalence_min_confidence` | `0.80` | `0–1` | sense same/compatible/conflict 判断门槛 |
| `personification_reverify_after_days` | `30` | `1–365` | verified 进入复核窗口的天数 |
| `personification_stale_after_days` | `90` | `2–730` | 无新证据后降为 stale 的天数 |

独立来源门槛在运行时还会执行安全归一，最低不得小于 2；`stale_after_days` 必须晚于复核窗口。`personification_probability` 仍为 0.30，玩梗抽样不会额外触发一条本来不会发送的群消息。

## 秘密和导出边界

以下内容不得返回 Agent、普通 WebUI JSON、日志、Trace、审计详情、诊断包、知识库或 Data Transfer：

- Cookie、Token、验证码、手机号、设备标识和平台请求签名；
- 二维码登录 session 与管理员/device owner 绑定信息；
- Playwright browser profile 路径及 profile 内文件；
- 脱敏作者指纹；
- 短期完整评论缓存、原始整页 HTML 和完整视频。

Data Transfer v3 只导出逻辑词典根、sense、短证据和学习事件。证据 URL 会移除 query/fragment，`author_fingerprint` 在包 schema 中不存在，导入后固定为空；旧 v1/v2 包继续可读，v2 包不能声明 v3 的 `meme_dictionary` dataset。

## 状态排查

| 状态 | 含义 | 处理 |
|---|---|---|
| `disabled` | 平台开关关闭 | 开启平台后再检查登录 |
| `login_required` | 未登录或登录过期 | 重新扫码/官方确认 |
| `ready` | 可进行只读请求 | 再检查工具授权和查询输入 |
| `manual_verification_required` | 官方页面要求人工验证 | 在官方页面完成，不要自动重试或绕过 |
| `risk_controlled` | 平台风控 | 暂停该平台，等待解除；使用其它平台 partial 结果 |
| `unavailable` | 浏览器、页面或适配器异常 | 运行健康诊断并检查 Chromium/Playwright 与页面改版 |

## 真实验收清单

四个平台的自动测试只能验证协议、隔离、过滤、解析和失败边界；首版完成仍要求管理员在真实账号上逐个平台执行：登录、重启后保持登录、搜索同一真实黑话、读取封面/正文/评论/回复、检查支持平台弹幕、从一份内容提取多个 claim、关闭平台后确认零访问、注销后确认 profile 删除，并确认全过程没有任何平台写操作。
