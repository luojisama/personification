# 2026-10-10 轻量化实施与离线验收

## 目标与基线

从 `6e6ddae` 继续已有 `context_only` 改造，在无真实机器的情况下减少可证明的无用计算，建立可重复的隔离验收入口。模型仍决定语义与工具需求，普通/YAML 的最终审阅、来源归属、权限和确认发送合同不变。

## 本轮计划

1. 检查现有轻路径、测试与父工程聊天模拟器，复用已验证的真实 processor 测试夹具。
2. 将仅供工具链消费的查询清洗、追问恢复及群主题扫描延后；已确认轻路径不执行这些工作。
3. 群历史合并、引用补充、权限过滤完成后再做一次历史投影，去掉被覆盖的首次会话序列化；私聊与早退路径保持原有输出。
4. 增加独立模拟验收脚本，运行真实运行时与普通/YAML 入口，使用模拟 Provider、临时 SQLite 和合成送达回执，拒绝真实网络连接；输出机器可读的验证证据。
5. 运行定向验收、完整 Python 回归、聊天模拟和语义扫描，审查差异后中文提交推送，并将结果写入知识库。

## 复用与取舍

- 使用项目已有 pytest、`tests/_loader.py`、`scripts/quality_eval/pipeline_adapter.py` 和 `httpx.MockTransport`，无需增加运行时依赖。已有夹具可直接经过两个 processor 与 Agent，父工程聊天模拟主要验证 Agent 场景，作为补充。
- 2026-10-10 实际检索并阅读 [pytest monkeypatch 官方文档](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)：复用作用域内补丁和环境隔离；网络防护属于测试进程，不进入产品。
- 阅读 [Python ContextVar 官方文档](https://docs.python.org/3/library/contextvars.html)，使用现有上下文 API 验证异步并发作用域与取消恢复。未引入新的调度框架。
- 调查 NoneBug 的候选入口，但文档访问失败；现有夹具已满足本轮入口与传输模拟，无须为此迁移框架。此次不作真实吞吐、RSS 或端到端时延提升声明，评价指标是无用操作次数及合同行为。

## 验收范围

验收覆盖轻路径单次生成、无工具 wire、保留审阅与记忆采集，普通/YAML 真实入口、非轻路径保留工具能力、取消与并发隔离、失败静默、未知送达不重放以及会话资源回收。每项以实际执行的测试与报告为准；真实 LLM 输出质量、QQ/TIM 协议网络、VPS 性能和长期驻留稳定性不属于离线模拟能证明的结论。

测试产物统一位于 `D:/test_artifacts/personification/2026-10-10-lightweight/`。

## 本地模拟入口与已验证结果

从插件目录使用项目解释器运行：

```powershell
& D:/bot/nonebot/shiro002/.venv/Scripts/python.exe -B scripts/lightweight_acceptance.py --output D:/test_artifacts/personification/2026-10-10-lightweight/final-simulation
```

脚本只收集显式合同测试集，不启动插件注册或真实 Provider。进程内拒绝出站连接、DNS、UDP、子进程、隔离输出目录以外的 dotenv/SQLite 访问和文件写入；Windows asyncio 内部 socketpair 仅在标准库同步实现范围内放行。它是测试隔离防线，不是用于运行任意不可信程序的系统级沙箱。仓库已有 loader 与空配置发现替身避免 pytest 包发现触发插件启动。测试使用模拟身份和合成回执。

`report.json` 包含起止时间、Python 版本、关键源码及测试 SHA-256、每项结果与实测调用账本；`junit.xml` 可供测试工具消费，`pytest.log` 保存日志。重新运行先删除旧结果，违规访问即使被业务异常处理吞掉，也会使验收退出码非零。

2026-10-10 最终定向模拟为 **146 passed / 33.75 秒 / blocked_accesses=[] / exit_code=0**，使用 Python 3.13.6。此耗时仅是本机模拟测试执行时间。

| 验证项 | 实际结果 |
|---|---|
| 显式或预计算语义派生的轻路径 | 查询提取、查询清洗、追问恢复、群主题扫描不再执行；输入和审阅保留 |
| 群历史投影 | 合并后投影一次，保留引用、时间、权限过滤、Bot 归属、摘要降权与当前消息末尾顺序 |
| 普通/YAML × 私聊/群聊 | 每轮语义帧 1 次、生成 1 次、最终审阅 1 次，共 3 次模拟 Provider 调用；工具 schema 与原生搜索均为空/关闭 |
| 两轮并发 | 各生成一次，上下文身份不串用，审阅仍处在各自身份内 |
| 取消 | 生成任务取消传播，Provider finally 完成一次，不启动补发 |
| 外发与故障 | 复用现有普通/YAML、回复缓冲、generation fence、后台学习及 QQ outbound 合同测试 |

## 完整回归与失败收口

完整 Python 测试在 D 盘复制的版本化源码树执行，凭据目录与 env 指向隔离位置，子进程继承网络和数据库防护。实际生产改动及相关测试的复制树 SHA-256 已核对一致。初轮 **3839 passed / 24 failed / 10 warnings / 810.28 秒**，失败逐项处理如下：

- 19 项来自一次性全量守卫误解析 Windows `file:///D:/...` SQLite URI。修复 URI 解码后仍仅允许隔离根内数据库，相关模块 44 项通过。
- 1 项依赖被 Git 忽略的安装数据 `data/meme_seeds.json`。测试改用临时目录内合成种子，验证真实导入与查询，不读取本机数据，也不声称检验了发布资产的完备性。
- 4 项多轮历史检查暴露了模拟 Provider 的阶段识别问题：它将普通生成用途 `reply` 中的 `speech_act` 文本误判为规划请求。替身现按真实用途区分普通生成 `reply`、Agent 生成 `reply_generation` 和 `semantic_frame`，并明确断言首轮生成哨兵文本。原有确认历史、普通 assistant 归属与 YAML `[我]` 归属断言全部保留。

全部失败所属四个模块定向复验 **57 passed**；随后将多轮历史检查加入正式离线组合，最终 **146 passed**。没有第二次完整全量运行，不表述为一次全量全绿。完整回归的 10 个警告为依赖弃用、重复 ZIP 测试样本及 xunit2 不兼容 record_property；正式模拟设置 xunit1，最终没有警告。

额外 8 类负例验证了网络、dotenv、SQLite、子进程及隔离目录外写入均被拒绝，fake pytest 返回成功时 runner 仍失败；中断不会复用旧 JSON/JUnit。聊天模拟 **9/9**、语义扫描、项目 Ruff 0.16.9 门禁、新增代码 F401/F821 与 `git diff --check` 通过。保留报告和复现脚本，临时数据库及大文件测试夹具交付前清理。

验收记录与原始日志分别在本轮 `final-simulation/`、`full-review/` 和 `guard-probes/`。源码提交与推送不包含部署、真实外发或生产性能验收。
