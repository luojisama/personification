# plugin_invoker

让 bot 能够**代为执行**同一 NoneBot2 实例上安装的【其它插件】的命令，并把这些插件本来要发出的
消息捕获下来，交给 LLM 用人设语气转述给用户——而不是让用户自己去敲命令。

与 `plugin_knowledge`（只能“描述”插件）互补：`plugin_knowledge` 负责定位插件与命令，
`plugin_invoker` 负责真正“使用”它们。

## 工具

- `invoke_plugin(plugin_name, command_text)`：在内部以当前用户的身份把 `command_text`（如
  `/天气 北京`）分发给目标插件，捕获其输出并返回。返回文本会作为 tool 结果进入 Agent 上下文，
  由 Agent 用人设语气转述。

## 机制

1. 校验 `command_text` 的前缀确实属于 `plugin_name` 的某个已索引触发方式（`triggers`），防止 LLM
   把命令打到别的插件。
2. 危险/管理员命令过滤（封禁、踢人、删除、关机、全局开关等）+ 可配置 allow/blocklist + 排除
   personification 自身命令（防递归）。
3. 克隆当前消息事件、替换为目标命令、打 `_personification_synthetic` 标记，用一个子类化的代理 Bot
   拦截所有发送类 `call_api`（缓冲而非真正发出），再经 `nonebot.message.handle_event` 重新分发。
4. personification 自身的各 matcher rule 顶部会检测合成标记并短路，避免递归。

## 安全

默认**总开关关闭**（`personification_plugin_invoker_enabled=False`）。相关配置见 `config.py`：

- `personification_plugin_invoker_enabled`
- `personification_plugin_invoker_allowlist` / `personification_plugin_invoker_blocklist`
- `personification_plugin_invoker_max_calls_per_turn`
- `personification_plugin_invoker_capture_timeout`
- `personification_plugin_invoker_max_output_chars`
- `personification_plugin_invoker_extra_danger_keywords`

> 这是一个 per-request 工具：它需要当前的 `bot`/`event`，因此由
> `handlers/reply_pipeline/pipeline_context.py` 在每次回复时通过
> `build_invoke_plugin_tool_for_runtime(...)` 注册，而**不是**作为静态 skillpack 的 `build_tools`。
