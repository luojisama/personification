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

`--behavior-source server` 默认只从显式传入的 `env.json` 读取行为白名单。它不会搜索或读取 `.env`、`.env.prod`、`runtime_config.json`、provider pool、Cookie、Token、路径或任何外部服务开关；生产 `ConfigManager` 已将 `env.json` 作为 WebUI 管理的权威层，旧环境变量只参与首次导入缺失字段，不能在这里反向覆盖它。

若 VPS 仍有需要保留的已生效遗留引导值，先在受控环境中导出**脱敏** profile，再显式传入 `--behavior-snapshot /opt/.../behavior-profile.json`。profile 仅包含 `{"behavior": {"personification_...": ...}}` 中的行为白名单字段；评测会用生产 `Config` 的默认值和类型校验补齐遗漏字段。它不会自行读取生产 Secret 或启动生产服务。运行 manifest 的 `behavior_snapshot` 记录每个有效字段及来源（`env.json`、`defaults` 或 `explicit_redacted_snapshot`）；其中 `personification_system_prompt` 和 `personification_core_values_prompt` 只写 SHA-256、长度与来源，正文仍只保留在本次进程内供模型使用。白名单只限定可读取的配置键，不代表任意文本字段都会自动脱敏；导出 profile 前仍须自行移除不应进入评测的内容。独立的 `fixture_overrides` 记录评测强制的隔离路径、捕获 Bot、外部工具禁用和 `hash_bow` embedding，二者不可混同。

案例 ID 以 cases.jsonl 为准。无 --case 时选择 dev 集；holdout 必须显式选择。
SQLite 额度全阶段复用同一文件，总限 1500；预扣且失败不退款，重开不可提高已有上限。stage-calls 是案例间停止阈值，单案例内部仍以全局1500为硬限。恢复运行跳过已有结果，包括失败记录，不自动重放。

默认入口为普通消息处理器，由人格类型进入普通/YAML路径，使用独立数据库、DataStore和捕获发送的 Bot。外部工具注册被隔离，所有模型用途共用同一请求额度。报告保留各轮输入、回复、合成回执、用量及Trace ID。合成确认仅标 capture_confirmed，不代表 QQ 实测。`--runtime-path agent_fragment` 保留早期 Agent + 最终审阅片段用于连通诊断。fakecaller 结果明确标 simulated。

## 当前覆盖限制

80个场景、群私各40，dev60/holdout20，普通/YAML各40。当前处理器适配覆盖纯文字多轮、真实隔离 MemoryStore 的预置记忆和合成回执。带 coverage_requires、媒体、工具事件或记忆更新事件的案例仍明确阻断；记忆更新 CAS 的隔离单测不代表完整会话更新链路已覆盖。没有真实送达回执的结果一律不标 delivered。

媒体 fixture 只验证本地文件传输与 owner/media ID 绑定。1×1 PNG 和通用诊断视频、音频没有案例对应内容，始终标为 transport_only / case_media_asset_missing，不可用于媒体回复质量评分；语料中的 summary/transcript 不会提升为可信工具证据。

`grade_run.py` 对已保存的不同版本同案例进行两次反序盲评，沿用相同 budget-db；费用未配置时保持未知。`grading_provider.py` 只将已完成评分的 JSONL 提供给 Promptfoo 导出，不另外调用模型。模型评分是辅助证据，顺序不一致标未评分；真实平局单列。汇总未提供预期案例清单时不能宣布完整覆盖。

批量评测前须检查案例语义、真实请求计数和每轮预期。脚本错误只保留机器码与异常类型，不输出请求 URL、Key 或原始异常正文。

## 实际入站与历史回放

独立 QQ 测试 MCP 留在插件外。取得指定会话的真实入站后，可将事件写入隔离案例，再通过此评测入口生成候选；`capture_confirmed` 仍不是 QQ 送达，实际外发必须另行核验 MCP 回执，不得重放 unknown。

案例可显式提供 `bot_id`、ISO 8601 `current_time` 和 `seed.session_history`。历史记录必须标明当前 `session_id`、`group_id`（私聊为空）、`user_id`、`role` 与 `content`。已发送的当前 Bot 回复需同时标记 `bot_id`、`delivery="confirmed"`、`source_kind="bot_reply"`、`is_bot=true`；不得把 Bot 演示文本中的“测试输入”重新拆成用户历史。只接受 user/assistant 角色，拒绝跨私聊用户、跨会话、未知送达和其它 Bot 的 assistant 记录。原始案例与真实账号历史保存在测试产物目录，不提交进通用语料。

预置 assistant 历史不计入本轮 `confirmed_history`。普通路径保留其 assistant 角色，YAML 路径沿用生产的带说话人历史投影；这不是将历史提升为可信人格指令。

评测结果保留 `failure_events`（调用用途与错误类型）和 `optional_diagnostics`。仅明确标记的 `memory_query_plan` / `memory_recall_gate` 超时或取消，在主链已经正常完成或选择沉默时不再覆盖为 failed。其它调用失败、主链失败、外部取消和总调用额度耗尽仍保留原有失败/停止行为；可选阶段未完成也不能被解释为记忆能力通过。
