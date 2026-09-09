# 全场景记忆与社交更新：实现与验证记录

基线：`84d525aa1cea3cd4d325bcf7d94489a0b4c77da7`。工作区：`D:/bot/nonebot/shiro002/plugin/personification`。本记录随验收更新，不能把开发完成、源码推送、服务器部署、真实社交回执视为同一件事。

## 已实现的链路

- 默认 `personification_memory_retrieval_mode=algorithm_llm`：版本化 SQLite FTS5 中文 unigram/bigram、拉丁词和已确认别名，不需要 embedding 模型。写入、查询、重建均不创建默认哈希向量；已有索引保留。有效旧 embedding 配置迁移保留为 `hybrid_api`。
- 自动查询最多四组，允许复用 TurnPlan；时间范围与当前状态引用经过结构校验，不能生成 SQL 或改变身份。全文候选在 SQL LIMIT 前过滤身份，最终仍通过既有权限与 LLM 语义闸门。总时限默认五秒，失败有独立诊断。
- normal/YAML 使用统一 PreparedMemoryContext；文本 Agent、主动群聊/私聊及 QQ 空间生成路径通过 locally bound provider 准备上下文，作用域贯穿实际工具调用与审核。历史带时间、发送者、引用、来源；较小模型回退可重新裁剪结构化历史。
- 管理员可通过 `/api/personas/scoped-sharing` 对指定修订的单条画像授予/撤回跨场景共享许可；默认不共享，内容或来源修订后原许可自动失效，跨 Bot 不共享。这个入口不接受模型权限指令。
- 按平台/Bot/群/用户保存 v3 画像，私聊使用空群的专用作用域；旧无作用域画像不重复注入新路径。修订使用 CAS、generation guard，并在独立历史表保留旧版本。原文仍在既有消息/归档库。
- 群与私聊画像批处理使用持久 scope 计数、静默时间、冷却及每日 API 预算。调度 sidecar 不复制私聊正文。重启重新读取受限来源；删除取消后台任务并删除派生画像；全量清空同步删除 v3 文档、修订历史与共享许可，防止旧任务重建。
- SocialDecision 由 LLM 提供参与/沉默/延迟及简短动机；程序验证权限、冷却、事件幂等和真实回执。未知发送不重放同一事件，QQ 空间与聊天按同 QQ Bot 身份协调。点赞加评论保留子动作独立回执。
- 表情视觉元数据包含 OCR、动作、动画变化、字面情绪、交际意图、适用/不适用场景和置信度；视觉缓存与人设适配分开。低置信度不自动删图。自动选择在无 API、单候选或 API 失败时也不直接选首项；单候选仍可被模型拒绝。重标持久任务串行、有界、可暂停/恢复，失败停止自动重试。
- 人设界面增加三层投影、版本对比和六场景真实 API 预览；算法索引状态与重标进度使用管理员接口。前端产物同步构建。
- QQ 空间修正响应分类、未知回执及楼中楼失败不降级；仍复用原有 httpx 服务、能力矩阵与操作协调器。

## 外部依据与复用边界

使用现有 SQLite、Provider、Evolves、发送账本与视觉调用器；没有新增本地模型或模型权重。

- SQLite FTS5 官方文档：https://www.sqlite.org/fts5.html 。中文短词使用显式字符投影，不只依赖 trigram。
- Generative Agents：https://arxiv.org/abs/2304.03442 。采用有时间与证据的经历/状态分层，并非复刻完整模拟器。
- LongMemEval：https://arxiv.org/abs/2410.10813 、LoCoMo：https://aclanthology.org/2024.acl-long.747/ 。用长期事实变化和时间连续性组织回放；外部指标不作为本插件成绩。
- Mem0：https://github.com/mem0ai/mem0 。不引入完整存储框架以免重复 SQLite 与权限体系。
- aioqzone：https://github.com/aioqzone/aioqzone 。仅作协议交叉参考，未复制 AGPL 源码或替换 httpx。

远端 `gemini-3.8-flash-high` 只读审计的建议逐项复核。例如它建议把 FTS 表名改成别名；本地 SQLite 最小实验表明当前表名 MATCH 正常，因此没有采纳该错误建议。重建路径绕回哈希的问题则经源码确认后已修复。

## 可复现验收与实际限制

隔离产物：`D:/test_artifacts/personification/social_memory_update/`。

- `global_scene_memory_report_final.json`（与 v5 相同固定语料）：100 个合成记忆/社交合同案例，三轮共 300 次真实 SQLite/状态/决策代码回放。全部通过是合同测试结果，不代表 LLM 自然度或线上召回率。早期 v1/v2/v3/v4 报告有测试设计缺陷，不能用作验收。
- `remote_memory_actual.jsonl`：远端 Gemini、3 个合成时间场景、4 种输入变体、各重复 3 次，36 次调用全部返回文本。无时间历史的次日问候三次都继续催培训；时间化输入三次都改为询问昨天是否已结束。它是输入变体对照，不是完整插件线上 A/B。
- `remote_vision_actual.jsonl`：50 次合成视觉 API 调用，49 次结构化结果、1 次 RemoteModelError。调用延迟 P50 7.47 秒、P95 15.50 秒。该批图片只有五类重复图形、部分字面标签与画面冲突、初版 GIF 联系表缺少明确帧序，不能据此确认理解质量达标；自由文本动作不能以完全相同字符串作为语义准确率。已给回放联系表补帧号与时间。
- `remote_vision_v2_comparison.json`：重新建立十类、50 个不同素材（20 静态、30 GIF），旧/新视觉提示词各执行 50 次真实 API 调用，两版均 50/50 返回结构化结果。同一 NFKC/空白归一化规则下，清晰 OCR 两版均 45/45，不确定性两版均 5/5；方向关键词命中从 19/30 到 20/30，结构字段完整度从 99.2% 到 99.6%。方向指标只统计限定字段中的关键词，不能视为动作理解准确率；人工检查发现一些错误把移动主体当成静态。新版本 P50 7.21 秒、P95 9.95 秒，旧版 7.56/10.13 秒，单轮小样本不证明性能提升。
- 新视觉回放 usage 合计 input=105990、output=19420、total=161895；total 包含服务报告的额外 token，不能简单用 input+output 替代。素材无用户图片/聊天；这仍不替代真人素材盲评、人格一致性及实际发送选择验收。
- `remote_selection_summary.json`：相同 50 个合成场景和受控候选，基线 `84d525a`/新版的实际 `choose_sticker_for_context` 函数各执行 50 次 Gemini API 选择（以 AST 隔离执行，候选检索固定，未外发）。两版均无选中异场景候选；基线选目标 35 次、保留文字 15 次，新版选目标 25 次、保留文字 25 次。这里能确认新版更愿意不发图，不能单凭少发图证明更自然。新版输入 token 合计 66427，对照 80167；P50 4.52/4.19 秒、P95 5.99/9.25 秒，费用仍缺真实单价。
- `blind_vision_review.jsonl` 为 50 对匿名 A/B 视觉结果，评分留空，答案映射单独保存。已准备盲评材料，不冒充已完成人工盲评。
- `semantic_scan_final.txt` 通过；`chat_sim_final.json` 9/9 通过；`frontend_final.txt`：37 个测试文件、143 项测试全部通过。最终全量结果与修复后复验见文末。
- WebUI 已用隔离 Vite + 只读 mock API 检查索引卡显示；mock 的 118/120 等数字仅用于布局验证，不是生产覆盖率。
- API usage 已记录；未获得本服务的真实计费单价，费用不填写为零。尚无人工盲评自然度结论。

## QQ 空间生产边界

2026-09-08 只读检查：服务器运行提交为 `5c2e797`，未部署本更新。现有凭据读取自身动态返回 HTTP 200、code=0、subcode=0；既有扫描状态显示读到 5 条动态，最近错误为 `qzone_read_only`。没有发布、点赞、转发或评论验证，因此不能标记这些动作已恢复。读接口成功不能升级写能力。

## 迁移与回滚

部署前备份配置、主 SQLite 数据库、memory_palace 数据及 DataStore sidecar，停写后做一致性备份。本轮不删除原始消息或旧向量索引。

- `algorithm_llm` / `hybrid_api` 控制默认检索，后者要求已有有效 API embedding 配置。
- `personification_memory_query_planning_enabled=false` 可单独停查询规划，保留词法检索和语义闸门。
- 原有主动社交开关与 QQ 空间只读/写入权限仍生效；回滚功能不得改变未知发送记录为可重试。
- 表情后台重标可暂停，保留原文件；视觉标签与模型/提示词版本相区分。
- 代码回滚应恢复已备份配置与兼容的 schema；保留新增表通常不妨碍旧代码，但旧代码不认识新的 scoped profile 历史。不要用模型重造已经丢失的原文。

## 最终复验记录

- 默认禁用 API embedding 的 hybrid 分支已增加“禁止调用 embed_text”回归；原文/FTS 可用，重建报告 embedding_unconfigured，不生成哈希。
- 最新有针对性测试：检索/索引/规划/主动/QZone 102 项通过；QZone/运行时/画像/私聊 77 项通过；具备实际可选 Satori 依赖的 Satori+YAML 18 项通过。
- `satori_final_main.txt` 的桥接测试关闭与本用例无关的表达分支，使用显式 text TurnPlan 和事件等待，仍断言真实 processor 输入、媒体去重与严格发送回执。
- 暂存源码 AST 解析 78 个 Python 文件通过，diff 检查通过；未暂存 `_reference/` 或 API 产物/凭据。

- 最后一次全量 `full4.txt`：3432 passed、1 failed、4 warnings，871.14 秒。唯一失败为本轮运行启动时已加载的旧 Satori 测试夹具；其后明确隔离表达支路，主模型同依赖环境复验 `satori_final_main.txt`：Satori+YAML 18 passed。没有再次把 3433 项整套重跑成一份全绿日志，不能写成“全量 3433 全绿”。另两项 Pydantic 弃用与两项故意重复 ZIP 条目警告来自既有依赖/安全用例。
- 源码交付不代表生产上线；服务器未部署本次改动，QQ 空间写能力没有真实验收。人工盲评与生产交互质量仍待后续验证。
