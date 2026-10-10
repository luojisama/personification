# Telegram 群聊主动发话

`flows/telegram_proactive.py` 提供已有群聊空闲任务的 Telegram 传输适配。复用 `proactive_flow` 的话题提示、时间上下文、文本清理和最终审阅，以及 `social_decision` 的持久外发预留；不启动另一套模型或将 QQ 工具注册表带进 Telegram。

## 注册

在现有 scheduler 初始化时调用 `register_telegram_proactive_job`。必需参数为 `scheduler`、`plugin_config`、`get_bots`、`load_prompt`、`call_ai_api`、`get_now`、`load_proactive_state`、`save_proactive_state`、`logger`；这些与现有 `FlowSetupDeps` 同名。可以额外传入 `review_call_ai_api`，否则沿用已有主动消息审阅所用的 `call_ai_api`。

任务 ID 为 `personification_telegram_group_idle`，间隔沿用群聊空闲检查配置，单实例运行并合并错过的触发。`build_telegram_idle_checker` 可单独构建回调用于测试或统一调度。

## 开关和边界

- 必须同时开启插件总开关、主动消息总开关、群聊空闲发话开关，以及具体 TG 群/Topic 的 `proactive_enabled`。新房间默认关闭，连接范围必须允许群聊。
- 沿用静默时段、空闲时长、主动概率、单房间每日上限和持久冷却。不自动发现或主动联系私聊用户，不发送频道动态。
- 只读取 `scope_key`、Telegram 平台、对应 Bot namespace 严格匹配的群记录；未观察到真实用户消息的房间保持静默。旧 QQ 主动任务过滤 TG Bot，不再误选 TG 执行 QQ API。
- 当前薄适配只主动发送经过最终审阅的文本。情绪或关系没有当前房间的可信资料时，不从 QQ 全局状态借用。
- 模型生成和审阅期间如果有新消息、消息被编辑、房间权限关闭或 Bot 下线，未发送草稿放弃。
- 使用 `telegram_actions.send_parts` 的逐段确认回执。未知结果会持久封存，不换措辞重发；仅实际确认的段落写入群记录和 `group_<scope_key>` 会话历史，部分送达不会算整批成功。

本地测试使用模拟 Bot、隔离数据库和真实共享最终审阅函数，覆盖默认关闭、Topic 路由、跨平台隔离、审阅否决、新消息导致草稿过期、模型等待期间关停、部分送达及未知送达不重放。没有对真实 Telegram 群进行主动外发测试。
