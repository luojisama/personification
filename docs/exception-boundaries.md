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

## 核验方式

修改异常边界时优先用隔离故障注入验证：实际发送次数、confirmed/unknown 状态、历史写入、取消传播、锁/任务释放。既要测试单个失败，也要测试“已发送后诊断失败”“取消时清理失败”等组合。不因不完整测试替身而给生产代码新增 `getattr` 或吞错。

2026-09-28 的清理覆盖普通回复、YAML 回复、Trace 与消息缓冲四个模块；其余模块仍需按调用链逐项审查，不能宣称仓库的全部异常处理已清理。AST 统计范围为 Git 已跟踪 Python 文件，排除 `tests/` 与 `_reference/`；`except Exception: pass` 与裸 `except:` 分开统计。数量用于定位，不代表每处都是缺陷。

参考资料（2026-09-28 核验）：

- [Python 异常处理](https://docs.python.org/3/tutorial/errors.html)：捕获预期异常、异常传播与清理职责。
- [Ruff BLE001 上游规则](https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/rules/flake8_blind_except/rules/blind_except.rs)：宽泛捕获检查，重新抛出与记录异常的边界。
- [Ruff S110 上游规则](https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/rules/flake8_bandit/rules/try_except_pass.rs)：定位静默吞错。规则只能提供候选，不能判断 QQ 外发、取消与生成替换语义。本轮复用现有模块和 Python 标准库，不引入新的异常包装框架或运行时依赖。
