# 日常对话轻路径与模型容量

## 执行合同

`personification_context_only_chat_enabled` 默认开启。只有成功的 LLM 语义判断明确为低歧义 banter，且没有研究、外部证据、工具、深度回忆、记忆查询、媒体或动作需求时，才进入 `context_only`。普通与 YAML 共用策略；未知、降级或冲突判断仍走 Agent。开关关闭后恢复原路径，不改变全局 Agent budget/disclosure 配置。

轻路径保留本地人格、群风格、用户画像、已有状态、来源与时间标记及近期上下文。私聊不加载群资料；权限与归属过滤继续生效。跳过自动记忆检索、查询规划、记忆语义筛选、时态状态刷新和第二次 Agent 候选召回，不关闭确认发送后的长期记忆采集。

轻路径不装配工具/研究提示，不启用原生搜索，只进行一次回复生成，再走现有审阅与外发合同。意外工具调用拒绝执行，空响应或失败不会升级到工具链。外部请求失败与内部编程错误仍保持可诊断，不用固定话术补回复。

策略确定后最多18秒，并受已有更早截止时间约束；Agent生成截止时间为总截止前5秒，留给质量与外层最终审阅。这个时间包含本地上下文准备，不包含前置语义判断，不是端到端速度承诺。普通研究/动作预算不变，模型用途绑定不变。

## 容量与配置来源

未配置容量的模型默认上下文131072（128K），默认有效输入限制65536（64K），输出预留仍为8192。输入限制为窗口的一半，并受输入硬上限及输出/思考/安全预留共同约束；显式配置的容量继续优先。此默认值是本地预算回退，不代表上游实测容量，也不能根据名称含 Gemini 推定百万容量。

模型级正容量优先。旧Provider容量仅补给原 `model` 完全匹配的模型；`legacy_capacity_model_id` 保存该来源，避免切换默认模型后二次规范化造成容量串用。其它模型的零值仍为未配置。

Gemini模型发现保留 `inputTokenLimit`、`outputTokenLimit` 为 `reported_input_token_limit`、`reported_output_token_limit`。它们是上游报告的能力元数据，不是实测容量。界面允许管理员将同一模型报告的输入上限明确采纳为保守窗口和最大输入，不覆盖已有正值，不累加输出能力，也不把输出能力上限作为每次请求的输出预留。采用输入上限作为保守窗口不等于声明它就是完整上下文容量；实际输入仍受比例和预留约束。

管理员 `POST /config/provider-budget` 只计算配置草稿的本地预算，不发上游请求、不持久化配置；响应仅包含模型及预算结果。保存和重载后的实际请求trace才是生效证据。

## Trace读法

- `actual_steps` 旧字段表示上限。新字段 `effective_max_steps` 为上限，`executed_steps` 为已执行模型步骤，`tool_calls_executed` 为业务工具调用计数。
- 披露 `mode=off` 表示全量披露，不能理解成禁用工具。runner的 `wire_schemas` 是交给Provider的schema数，Provider规范化后的最终数量以 `provider_schema_prepare` 和 `provider_request` 为准。
- `local_context_only` 表示只加载本地资料，分别记录画像、历史和状态数量；`injected_count=0` 不代表整个记忆功能失效。
- `provider_context_budget_exceeded` 是本地预算拒绝。详情只带计数、来源和失败阶段，不含提示词或工具参数；不应展示成一般内部错误。
- 请求用途区分 `semantic_frame`、`memory_query_plan`、`memory_recall_gate`、`memory_state_refresh`、`reply_generation` 与 `reply_review`。用途标签不更换实际模型绑定。

## 验证与上线边界

本地使用隔离数据和模拟Provider，验证真实普通/YAML入口、无工具wire、记忆跳过、审阅截止时间、确认/未知回执，以及容量继承和界面。源码交付不等于VPS部署或真实Provider/QQ验收。

2026-10-02本地验收：完整Python测试3816通过，前端43个文件174项测试通过，聊天模拟9/9通过，语义扫描、项目Ruff检查、类型检查与生产构建通过。隔离mock浏览器检查1440×1000桌面和390×844窄屏的容量采纳及Trace详情，无横向溢出，console无错误或警告。证据位于 `D:/test_artifacts/personification/context-only-20261002/`；这些结果不代表真实上游P50/P95。

用户提供的VPS脱敏核对确认：运行版本为 `6f35a8672ac9addc0bc6a5ba24877892b808c59d`，两个Provider的容量字段均未设置，且无模型数组；此次保守回退直接来自缺失容量配置。源码升级不会凭空补出未知别名的容量，仍需管理员依据接入服务元数据配置并重载验证。

VPS只需提供运行提交号、provider/model ID、用途绑定、模型级与旧Provider级数值容量以及生效值。不得导出Key、Cookie或完整配置。对于 `gemini-3.8-flash-high`，先确认接入服务的映射，再配置该模型条目；不得直接套用标准模型容量。

2026-10-02核验的官方来源：[Models API](https://ai.google.dev/api/models)、[Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)、[Function calling](https://ai.google.dev/gemini-api/docs/function-calling)。标准模型文档的输入1048576、输出65536不能单独证明中转别名的容量。
