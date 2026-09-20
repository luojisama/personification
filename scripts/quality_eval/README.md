# 回复质量评测（仅开发工具）

固定 Promptfoo 0.120.0，兼容本机 Node 22.18.0 / npm 10.9.3。生产插件不导入此目录，也不依赖这些 npm 包。

## 本地工具检查

在本目录执行 npm ci；如先使用 --ignore-scripts，则需 npm rebuild better-sqlite3 安装已核对的原生 SQLite 依赖。
设置 PROMPTFOO_DISABLE_TELEMETRY=1、PROMPTFOO_DISABLE_UPDATE=1、PROMPTFOO_PYTHON 为项目 Python、PROMPTFOO_CONFIG_DIR 为测试产物目录。
`npm run eval -- -c canary.yaml --no-cache -o <artifact>/canary.json` 验证异步 provider/导出；该 canary 不调用模型，不能当作回复质量结果。

## VPS 真实入口

仅从 Git 拉取本目录后运行。凭据只读自服务器配置；真实 Gemini 限定 pool[1] 的 gemini-3.8-flash-high、自建端点、Bearer、非流式，禁用认证协商与隐式重试。

```sh
/bot/shizuku/.venv/bin/python /bot/shizuku/plugin/personification/scripts/quality_eval/run.py --config-path /bot/shizuku/data/personification/env.json --artifact-dir /opt/pf-quality-20260920/baseline --budget-db /opt/pf-quality-20260920/global-budget.sqlite --case p01-casual
```

案例 ID 以 cases.jsonl 为准。无 --case 时选择 dev 集；holdout 必须显式选择。
SQLite 额度全阶段复用同一文件，总限 1500；预扣且失败不退款，重开不可提高已有上限。stage-calls 是案例间停止阈值，单案例内部仍以全局1500为硬限。恢复运行跳过已有结果，包括失败记录，不自动重放。

默认入口为普通消息处理器，由人格类型进入普通/YAML路径，使用独立数据库、DataStore和捕获发送的 Bot。外部工具注册被隔离，所有模型用途共用同一请求额度。报告保留各轮输入、回复、合成回执、用量及Trace ID。合成确认仅标 capture_confirmed，不代表 QQ 实测。`--runtime-path agent_fragment` 保留早期 Agent + 最终审阅片段用于连通诊断。fakecaller 结果明确标 simulated。

## 当前覆盖限制

80个场景、群私各40，dev60/holdout20，普通/YAML各40。当前处理器适配覆盖纯文字多轮和合成回执。带 seed_memory 或 coverage_requires 的案例暂明确阻断，等待记忆、媒体、工具和社交fixture适配；不能仅凭模型读懂其文字就宣称行为通过。没有真实送达回执的结果一律不标 delivered。

`grade_run.py` 对已保存的不同版本同案例进行两次反序盲评，沿用相同 budget-db；费用未配置时保持未知。`grading_provider.py` 只将已完成评分的 JSONL 提供给 Promptfoo 导出，不另外调用模型。模型评分是辅助证据，顺序不一致标未评分；真实平局单列。汇总未提供预期案例清单时不能宣布完整覆盖。

批量评测前须检查案例语义、真实请求计数和每轮预期。脚本错误只保留机器码与异常类型，不输出请求 URL、Key 或原始异常正文。
