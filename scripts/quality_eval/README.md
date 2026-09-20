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

入口在隔离工作目录构造真实 run_agent + final_dialogue_gate、独立数据库和实际Trace。空工具注册表及不外发执行器不会访问QQ/QZone。报告保留各轮生成候选、最终回复、审阅动作、用量及Trace ID。fakecaller 结果明确标 simulated。

## 当前覆盖限制

80个场景、群私各40，dev60/holdout20。当前模型入口覆盖Agent与最终审阅，不等于完整 normal/YAML 处理器、真实媒体工具、记忆写入或发送回执行为。工具结果/媒体/送达类场景仍需真实隔离路径适配，不能仅凭模型读懂其文字就宣称行为通过。没有送达回执的结果一律不标 delivered。

批量评测前须检查案例语义、真实请求计数和每轮预期。脚本错误只保留机器码与异常类型，不输出请求 URL、Key 或原始异常正文。
