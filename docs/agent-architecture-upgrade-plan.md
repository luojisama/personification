# Agent 架构升级计划

更新时间：2026-07-01

## 目标

在不破坏“正常聊天语义 LLM-led”的前提下，把当前 Agent 从“能完整走工具循环并生成回复”继续推进到“能稳定决定怎么参与聊天、怎么执行外发动作、怎么被回放评估和灰度优化”的结构。

本计划不引入关键词/正则语义路由。代码继续负责上下文、工具、契约、预算、trace、持久化和兜底；模型继续负责是否回复、当前轮怎么理解、是否调用工具、证据是否足够、最终怎么说。

## 当前评分

当前架构约 7.6/10。

- 优点：完整 Agent 覆盖面已经很大；TurnPlan、语义帧、工具筛选、skillpack、trace、WebUI 观测都有基础；扩展能力主要靠工具而不是核心分支。
- 进展：`speech_act` 说话动作层、发送型工具 metadata 契约、第一块 `tool_contracts` 分层和随机插话结构静默门控已经落地，回复基调、外发工具静默收尾和“该沉默就沉默”的入口兜底不再只靠事后补救。
- 短板：runner 仍承担太多阶段；真实坏例回放和量化评估不足；延迟预算还没有充分按场景分层；短期话题状态还需要继续增强。

## 阶段一：说话动作层

状态：本轮已落地第一批，并补充随机插话结构静默门控。

新增 TurnPlan `speech_act` 字段，让模型在回合规划阶段明确最终可见回复承担的聊天动作：

- `participate`：参与讨论或闲聊推进半步。
- `answer`：回答问题。
- `ask_followup`：追问一个具体点。
- `clarify`：信息不足时短澄清。
- `tease`：轻吐槽或接梗。
- `execute_action`：主要执行会外发内容的工具。
- `source_summary`：基于证据做简明总结。
- `silence`：保持沉默。

落点：

- `agent/runtime/planner.py` 负责生成、解析、fallback 和 semantic frame 互转。
- `core/reply_style_policy.py` 负责把 `speech_act` 转成模型侧输出纪律。
- `core/response_review.py::arbitrate_reply_mode()` 在随机插话、非直呼、非连续独聊、语义帧建议沉默且目标不是 bot 时直接静默，避免低置信 fallback 继续放行到 Agent 生成观望式废话。
- 普通回复、YAML 回复、Agent runner 都注入同一说话动作提示。
- `reply_turn_trace` 记录 `speech_act`，方便后续观测和灰度。

验收：

- 普通回复和 YAML 回复在同一 TurnPlan 下得到一致的说话动作提示。
- “讨论、闲聊为主，而不是附和、感叹、转述”不再只靠事后 review，而是在生成前就进入结构化计划。
- `execute_action/silence` 场景优先静默，不用“先看看情况/等会再说”这种旁白代替沉默。

## 阶段二：工具动作契约

状态：本轮已落地第一批。

把发送型工具的副作用和最终回复行为写入工具 metadata，而不是在 `runner.py` 里维护工具名白名单。

新增 metadata 字段：

- `side_effect`：默认 `none`，发送消息类工具用 `send_message`。
- `final_behavior`：默认 `continue`，外发成功后静默类工具用 `silence_on_success`。
- `retryable`：默认 `false`，普通查证工具可标记为可重试，外发副作用工具默认不可重试，避免重复发送。

落点：

- `agent/runtime/tool_catalog.py` 提供默认契约和 `tool_runtime_metadata()`。
- `agent/runtime/tool_contracts.py` 读取契约决定工具结果是否直接收尾为 `[SILENCE]`，并承接直出媒体、图片生成失败格式化和意图推荐工具逻辑。
- `agent/runtime/runner.py` 只调用契约层，不再直接维护副作用工具收尾细节。
- QQ 表情、本地表情包、联网搜图直发等工具统一走 `side_effect=send_message` + `final_behavior=silence_on_success`。

验收：

- 新增发送型工具只要声明 metadata，就能在成功排队后静默收尾。
- runner 不再需要维护发送型工具名集合。
- 副作用工具不可重试，避免重复发图/发表情。

## 阶段三：runner 分层拆解

状态：本轮已落地第一块工具结果契约层。

目标：降低 `agent/runtime/runner.py` 的维护压力。

建议拆分：

- `planning_context`：意图、TurnPlan、query rewrite、候选工具提示。
- `tool_loop`：模型工具循环、预算、工具结果回灌。
- `tool_contracts`：副作用工具、直出媒体、失败格式化、静默收尾、意图推荐工具。
- `final_synthesis`：工具结果拟人化、证据综合提示、最终回复收口。

验收：

- runner 主函数保持编排视角，不再塞入所有判断细节。
- 新增工具契约或收尾行为时不改核心循环主体。
- 相关单测能直接测契约层，而不是整条 Agent 循环。

## 阶段四：真实坏例回放与量化评估

状态：本轮已落地冷启动坏回复质量样例与报表汇总。

目标：把回复自然度从“调 prompt 凭感觉”推进到“可回放、可比较、可灰度”。

回放集至少覆盖：

- 空泛附和、感叹。
- 转述群友原话或总结聊天内容。
- 旁白式“我先看看/等会再说/先围观”。
- 该沉默时插话。
- 该接话时冷掉。
- 发送图片/表情后又补废话。
- 高歧义梗或专名未查证就硬接。
- 图片/表情包场景主动讲解画面。

验收：

- 每个坏例有输入上下文、期望 `speech_act`、期望回复/沉默边界和失败原因标签。
- `scripts/replay_corpus.py` 能输出 Markdown 对比报告，汇总 plan diff、质量标签、回复边界和坏回复样例。
- 新 prompt 或结构改动先看回放集差异，再灰度上线。

## 阶段五：延迟预算分层

目标：短闲聊不要每轮都付完整查证/工具循环成本，复杂查询也不要被短预算截断。

建议策略：

- `speech_act in {participate, tease}` 且 `tool_intent=["none"]`：短预算、轻工具、少模型步。
- `speech_act in {source_summary, answer}` 且 `research_need>=medium`：完整工具预算、证据综合可用。
- `speech_act=execute_action`：优先动作工具预算，成功后静默。
- `speech_act=silence`：尽快结束，不进入可见生成。

验收：

- trace 中能看到本轮预算模式。
- WebUI 慢阶段能区分语义帧、TurnPlan、工具循环、证据综合、发送层拟人延迟。
- 短闲聊 P95 明显下降，同时复杂查询不退化。

## 阶段六：短期话题状态

目标：减少群聊接错话题、跨人拼接、问已知信息。

建议维护轻量 topic state：

- 当前话题摘要。
- 主要参与者。
- bot 上一句是否被接住。
- 当前消息是在接谁的话。
- 是否直接 cue bot。

这仍是结构化上下文，不是关键词语义路由。模型拿 topic state 做判断，代码只负责维护事实线索。

验收：

- 低信息跟帖优先沿用最近同一话题。
- 多人并行话题不跨人拼接。
- 被明确 cue 时不被相邻图片/表情覆盖。

## 阶段七：WebUI 诊断面

目标：让调参和排错能看见 Agent 的结构决定，而不是只看最终回复。

建议展示：

- `reply_action`
- `speech_act`
- `message_target`
- `output_mode`
- `tool_intent`
- 工具契约命中情况
- 预算模式和慢阶段
- review 是否改写/沉默

验收：

- 一条不自然回复能从 WebUI 看出是 TurnPlan 错、工具选择错、证据不足、final synthesis 跑偏，还是 review 没兜住。

## 阶段八：灰度与回滚

目标：每个高风险行为层都有开关、trace 和回退路径。

建议：

- `speech_act` 先默认启用提示注入，但 TurnPlan 接管仍尊重现有开关。
- runner 拆分保持函数签名兼容。
- 新预算策略先 shadow 记录，不直接改变生产超时。
- replay 通过后再考虑默认打开更激进的策略。

验收：

- 任一阶段改坏时能通过配置回退到上一层行为。
- 单测、语义扫描和回放报告能证明没有引入关键词语义路由。
