# API 记忆与长上下文更新

## 行为与运行边界

normal 和 YAML 在分流前共享 `PreparedMemoryContext`。历史原文带日期、发言人、消息 ID 和引用来源；存储为 system 的摘要进入模型时降为历史资料。自动召回结合最近对话，独立 API 状态抽取维护 planned/inferred/confirmed/cancelled 修订，真实与模拟层不互相更新。旧状态修订保留来源，删除会话同时清除关联归档和当前状态。

群聊扩展查询按平台/Bot/群限定，新原始记录写入来源字段；归属未知的旧记录不自动加入严格身份范围。未知记录仍保留，需要依据可信生产来源做单独迁移，不能凭当前登录 Bot 批量认领。当前状态和已有同身份待办的取消由 LLM 判断，不对“军训/放假”等词编写规则。

所有模型推理通过 API。SQLite、词法检索、向量相似度和 token 估算为本地普通计算，不下载或加载模型权重。

## 配置与迁移

新配置默认私聊 14 天/4000 条、群聊 7 天/12000 条。记录保存、检索候选和实际模型输入是不同范围。压缩事务先归档原文，再替换活动会话；摘要 API 失败不删除原文。

旧版全量 env.json 无法证明字段是否由管理员指定，因此不会按数值相等猜测并覆盖。ConfigManager 保存预加载来源和 `env.json.provenance.json`，管理台提示仍生效的旧限制。要在旧实例明确启用新范围，在配置中心设置以下字段；保存后新字段优先：

| 字段（均带 personification_ 前缀） | 建议值 |
|---|---:|
| private_history_days | 14 |
| private_history_max_messages | 4000 |
| group_history_days | 7 |
| group_history_max_messages | 12000 |
| memory_retrieval_days | 30 |
| memory_auto_recall_timeout_seconds | 5 |
| memory_auto_recall_candidate_limit | 32 |
| memory_auto_recall_inject_limit | 12 |
| context_input_ratio | 0.5 |
| context_safety_margin_ratio | 0.05 |
| session_compress_token_threshold | 0（按主路由预算） |

Provider 池每条路由支持 `context_window_tokens`、`max_input_tokens`、`max_output_tokens`、`input_token_limit`。必须填写所用服务实际支持的容量；未知路由使用保守的 32768 窗口，不按模型名字猜测百万上下文。输入预算默认上限为窗口一半，另受服务上限、输出/思考预留和安全余量限制。不能把模型的最大输出能力与本轮输出请求预算混淆。

每次 RoutedToolCaller 请求（包括工具循环、重试、回退）重新核算系统、历史、工具、媒体。支持计数接口的 caller 可提供 count_tokens；否则使用估算并按返回的原生 usage 校准，Trace 显示来源。媒体不会把 base64 编码长度当文本 token。YAML 保留进程内历史投影，备用路由更小时可重新选择历史；相关内部元数据不会发送给服务。

## API embedding

沿用 `real_embedding_enabled`、`embedding_provider`、`embedding_model`、`embedding_api_url`、`embedding_api_key`。支持 Gemini REST 与 OpenAI-compatible embeddings。不得把不支持 embeddings 的聊天接口当向量接口；管理台不因配置存在而标记“连通正常”。

启用后写入保留原文并排队，旧记忆按目标 API 版本补索引。版本绑定 provider、model、端点指纹、返回维度。旧哈希索引保留；API 索引覆盖完成后激活，重建中不混用不同向量。失败保留队列并明确诊断，召回可退回词法/时间证据。后台批量补索引，WebUI 重建按钮调用异步接口。API Key 只在原配置路径管理，不写进研究报告或 Trace。

`search_conversation_history` 工具查询当前身份范围的原始归档：默认近30天，days=0 允许无时间截断，返回数量仍有界。它使用词法检索，不宣称对全部原文执行了语义检索。

## 验证与回滚

确定性测试位于 `test_memory_temporal_context.py`、`test_context_budget.py`、`test_api_embedding_memory.py`、`test_history_config.py` 等。覆盖时间移动、摘要降级、原文归档、Bot/用户隔离、状态纠正、索引版本/竞态、媒体预算及备用路由。

`scripts/memory_api_replay.py` 是独立合成回放工具，只读取明确指定的 JSONL，不读取插件数据库或密钥文件。示例（API 凭据预先置于环境变量，不写进命令行）：

```powershell
python scripts/memory_api_replay.py --cases tests/replay_corpus/global_memory/memory_temporal.jsonl --endpoint https://YOUR-API/v1 --model YOUR-MODEL --repeat 3 --output D:/test_artifacts/personification/memory_update/api-replay.json
```

密钥变量默认 `PERSONIFICATION_REPLAY_API_KEY`。四个上下文变体输出 usage、P50/P95 和结果文本。用例的 expected_contains 仅作可复查的包含检查，不等同于独立语义评审或生产质量分数；实际对话自然度、费用与纠正复发率需要人工/API评审后验收。

上线前备份配置、来源侧车和数据库。可分别关闭 `memory_context_enabled`、`context_budget_enabled` 或 `real_embedding_enabled`；这些开关不删除新数据。回滚旧源码须恢复配套配置与数据库备份，而非假定旧版能理解全部新增表和投影。群内原始身份与长期记忆迁移、真实 API 回放、生产部署和 QQ 送达均是独立验收步骤，本地测试不代替它们。


### 本轮真实服务观察（2026-09-08）

生产配置只读核查显示真实向量开关开启，但 provider=hash_bow、模型名为空。现有 OpenAI-compatible 服务 /models 返回3个模型，没有列出 embedding 模型；目录不是所有能力的最终证明，因此 embedding 真实接入仍需明确模型和接口。

通过该服务的 grok-4.6 运行合成跨日对话：无日期基线35秒超时；带日期版本约10.05秒成功（输入320、输出650 token），带状态版本约11.53秒成功（输入305、输出801 token，包含服务报告的推理token）。两次均把对话接到假期，没有继续催培训；计划/确认的表达仍需更大样本检查。这些是直接API提示词回放，不是运行新插件后的生产QQ验收；基线失败，不能据此计算改善比例、稳定P95或质量不退化结论。
