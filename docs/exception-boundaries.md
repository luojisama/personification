# 异常处理与技术债清理边界

异常处理以实际失败行为为依据，不以减少 `try` 或 `isinstance` 数量为目标。外部消息、Provider 返回值、历史数据与配置的结构校验需要保留；已明确的内部对象契约不应靠多层默认值和吞异常维持表面成功。

## 责任边界

- Trace 写入由 `core/reply_turn_trace.py` 处理 SQLite / 文件系统故障。调用方直接调用，不再重复包 `except Exception: pass`。存储失败诊断只含操作名和异常类型，限频且不记录正文、身份或异常原文。内部编程错误仍可见。
- 恢复队列的存储失败由 `handlers/reply_buffer.py::_record_recovery_failure` 记录；失败不能证明已经安全入队，也不能把未知送达改为可重放。
- `CancelledError`、NoneBot `FinishedException` 是控制流，不应被 TTS、消息发送等普通失败降级分支吞掉。结束语音路径不能自动再发文字。
- 正在传播的主异常优先于诊断与清理的二次错误。清理必须释放本轮拥有的锁、任务及 ContextVar，不能用 `finally` 中的 `return` 消除取消。
- 发送后错误根据真实 `reply_delivery_started/confirmed/complete` 状态记录。Trace 是观测记录，不是发送回执；未知送达不能升级为成功或自动重发。
- 生成替换与抢占复用 `core/generation_fence.py`，不维护少一两个状态字段的本地副本或宽松 fallback。
- 分段、归属构造、固定正则结果转换等内部操作失败应暴露缺陷，不应偷偷发送缺少归属的消息或绕过命令检查。

## 后续治理的验收合同

- 调用兼容使用 `inspect.signature(...).bind(...)` 在执行前选择已支持签名。`core/call_compat.py` 只选择参数，不执行调用；签名不可检查时只使用规范签名一次。调用过程中抛出的 `TypeError` 不能触发第二次请求。内部确定接口不保留仅为旧测试替身服务的签名回退。
- QQ 原始协议回执中明确失败或未知的状态不能因携带 message id 升级为成功。框架已解包的 message id 结果和内部 `SendReceipt` 分开处理。发送已经开始后，超时、取消、未知回执不能换 Bot 或换内容重发。已确认发送后的历史、昵称、日志故障不能改变送达事实。
- DataStore 缺失记录可以初始化；已存在的坏 JSON 必须保留并阻止覆盖。更新对象要求原记录是对象，不能用空字典代替列表或标量。校验与写入处于同一事务，失败回滚；取消线程写入的等待必须覆盖实际 worker 生命周期。
- 连接初始化失败要关闭连接；关闭的二次错误不能覆盖初始化错误或取消。浏览器 context 仅在关闭成功后从池中移除，以保留失败后的清理能力。
- 配置更新按持久化、内存同步、服务 reload 顺序执行；上一步失败不能进入后一步。已落盘而 reload 失败要报告部分完成。
- Provider 路由切换必须基于可识别的上游故障。内部构造或处理错误直接传播。流式能力协商仅接受明确的不支持信号，并计入实际 wire 请求预算；已开始接收流后不再以兼容为由重发。图像能力降级不能偷偷丢弃参考图或指定模型。
- 每轮后台学习任务有明确 owner，并复用 `RuntimeTaskSupervisor` 的关闭路径；生成失效时取消，写入前检查 generation fence。完成后移除任务引用，不能按每轮 ID 无限增长监控记录。

## 职责拆分

本轮在行为修复后拆出独立职责，调用入口保持可追踪：

| 原模块 | 独立职责 | 目标模块 |
| --- | --- | --- |
| `handlers/persona_admin_commands.py` | Peer Bot 管理命令；权限检查仍在上层分发 | `handlers/peer_bot_admin_commands.py` |
| `handlers/reply_pipeline/processor.py` | 群聊批次历史、Agent 输入与媒体输入投影 | `handlers/reply_pipeline/input_projection.py` |
| `skills/skillpacks/tool_caller/scripts/impl.py` | Provider Token 与缓存用量字段归一化 | 同目录 `usage_normalization.py` |
| `agent/runtime/runner.py` | 主动学习后台任务启动与所有权注册 | 同目录 `background_learning.py` |

拆分不改变普通/YAML回复语义，不新增统一吞错装饰器。`core/error_utils.py` 中没有调用方的 `log_on_exception`、`swallow_and_log` 已删除；保留实际仍在使用的日志接口。

## 开发检查范围

仓库使用固定版本 Ruff 0.16.9，仅用于开发检查，不增加插件运行时依赖。`ruff.toml` 明确列出已治理模块，启用 `BLE001`、`S110`、`B012`、`F401`、`F821`。拆分时对原模块额外检查 `F821`，防止移动函数后误删仍被使用的导入。从插件根目录运行：

```powershell
python -m pip install -r requirements-dev.txt
ruff check .
```

真实外部边界或保护主异常所需的宽泛捕获可以使用附理由的局部 `noqa`，同时必须有相应故障测试；不允许整文件关闭规则。规则通过只证明所列静态约束通过，不证明所有异常路径、其余历史模块或生产行为已被验证。

## 行为回归

修改异常边界时优先用隔离故障注入验证：实际发送次数、confirmed/unknown 状态、历史写入、取消传播、锁/任务释放。既要测试单个失败，也要测试“已发送后诊断失败”“取消时清理失败”等组合。不因不完整测试替身而给生产代码新增 `getattr` 或吞错。

2026-09-28 第一批清理覆盖普通回复、YAML 回复、Trace 与消息缓冲；后续治理范围为上文列出的外发、存储、配置、Provider、任务生命周期及职责拆分。未列出的历史模块仍需按调用链逐项审查，不能宣称仓库的全部异常处理已清理。AST 统计范围为 Git 已跟踪 Python 文件，排除 `tests/` 与 `_reference/`；`except Exception: pass` 与裸 `except:` 分开统计。数量用于定位，不代表每处都是缺陷。

## 本阶段验证记录（2026-09-28）

- 最终全量：`3778 passed, 4 warnings`，相比上一阶段新增86个测试案例。警告为第三方 Pydantic 弃用提示和重复 ZIP 条目的故障测试。
- 聊天模拟9/9、semantic scan、WebUI JavaScript语法与现有静态检查、限定范围 Ruff、拆分原模块 F821、`git diff --check` 均通过。
- 本阶段基线为518个生产Python文件、2687个try、1846个宽泛捕获、283个pass-only；加入5个独立职责模块后为523文件、2705个try、1849个宽泛捕获、275个pass-only，裸except均为0。统计包含本轮新增文件，不把计数变化作为正确性指标。
- 全量测试曾发现拆分时遗漏仍被使用的导入，已恢复并重跑完整套件；回执与图像能力测试替身也已更新为真实协议契约，未为旧替身放宽生产判断。
- 最终日志：`D:\test_artifacts\personification\debt-plan-20260928\final2\`。本地隔离测试不代表真实Provider、QQ/QZone外发或生产部署验收。

参考资料（2026-09-28 核验）：

- [Python 异常处理](https://docs.python.org/3/tutorial/errors.html)：捕获预期异常、异常传播与清理职责。
- [Python Signature.bind](https://docs.python.org/3/library/inspect.html#inspect.Signature.bind)：调用前绑定参数，不能将函数内部 TypeError 当作签名不兼容。
- [Python asyncio 任务](https://docs.python.org/3/library/asyncio-task.html)：保留后台任务引用、取消传播和关闭等待；本仓库复用已有 supervisor。
- [OneBot v11 HTTP 通信](https://github.com/botuniverse/onebot-11/blob/master/communication/http.md)：原始响应的 status、retcode、data 契约；框架已解包结果另行识别。
- [Ruff BLE001 上游规则](https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/rules/flake8_blind_except/rules/blind_except.rs)：宽泛捕获检查，重新抛出与记录异常的边界。
- [Ruff S110 上游规则](https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/rules/flake8_bandit/rules/try_except_pass.rs)：定位静默吞错。规则只能提供候选，不能判断 QQ 外发、取消与生成替换语义。本轮复用现有模块和 Python 标准库，不引入新的异常包装框架或运行时依赖。
